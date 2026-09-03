"""The valuation engine: select methods, weight them, adjust once, report the gap.

The order matters and is the order of the standard itself.

    4. select the methods the stage allows
    6. run each one; a method that cannot be grounded excludes itself
    5. weight and blend what ran into a low / base / high range
    7. build the premium and discount factors from evidenced triggers
    8. apply them once, and record the bridge
    9. build the sensitivity grid
   10. price the round at each case
   11. measure the requested valuation against the range
   12. name the factors that will decide whether investors accept it
   13. score the confidence in the whole thing

Everything here is deterministic: the same profile produces the same numbers
every run. Nothing in this module calls a model.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile, Ledger
from ..models.valuation import (
    Adjustment,
    DISCOUNT,
    MethodResult,
    PREMIUM,
    ValuationAnalysis,
    ValuationRange,
)
from ..utils import formatting as fmt
from ..utils import normalization as norm
from . import (
    adjustments,
    comparable_financing,
    dilution,
    revenue_multiple,
    scorecard,
    sensitivity,
    venture_method,
)

RUNNERS = {
    revenue_multiple.NAME: revenue_multiple.run,
    comparable_financing.NAME: comparable_financing.run,
    venture_method.NAME: venture_method.run,
    scorecard.NAME: scorecard.run,
}

# Confidence thresholds against the points scored in `_confidence`.
HIGH_CONFIDENCE_POINTS = 7
MEDIUM_CONFIDENCE_POINTS = 4


def run(profile: CompanyProfile) -> ValuationAnalysis:
    """Produce the complete valuation analysis for a company profile."""
    ledger = profile.ledger
    analysis = ValuationAnalysis(stage=profile.stage)

    analysis.methods = _run_methods(profile, ledger)
    _weight(analysis, profile)

    baseline = _blend(analysis)
    if baseline is None:
        # Every method excluded itself. The engine reports that rather than
        # inventing a number to fill the space (section 17).
        analysis.range = ValuationRange(0.0, 0.0, 0.0)
        analysis.confidence = config.LOW
        analysis.confidence_reason = (
            "No valuation method could be grounded in the information supplied."
        )
        analysis.assumptions = _assumptions(ledger)
        analysis.comparable_note = _comparable_note(profile)
        return analysis

    analysis.baseline = baseline
    analysis.adjustments = adjustments.build(profile)
    analysis.adjustment_applied_share = adjustments.applied_share(analysis.methods)
    net = adjustments.net_adjustment(analysis.adjustments)
    adjusted, effective = adjustments.apply(
        baseline, net, analysis.adjustment_applied_share
    )
    analysis.net_adjustment = effective
    analysis.range = adjusted.ordered().widened()
    analysis.bridge = adjustments.build_bridge(
        baseline.base, analysis.adjustments, analysis.adjustment_applied_share
    )

    ledger.record(
        "weighted_baseline",
        baseline.base,
        config.CALCULATED,
        "weighted blend of the methods that ran",
        config.MEDIUM,
        unit="$ pre-money",
    )
    ledger.record(
        "net_adjustment",
        effective,
        config.CALCULATED,
        f"{len(analysis.adjustments)} evidenced factors, "
        f"applied to {analysis.adjustment_applied_share:.0%} of the blend",
        config.MEDIUM,
        unit="fraction",
    )
    ledger.record(
        "base_valuation",
        analysis.range.base,
        config.CALCULATED,
        "weighted baseline x (1 + net adjustment)",
        config.MEDIUM,
        unit="$ pre-money",
    )

    analysis.sensitivity = sensitivity.build(
        profile, analysis.range, analysis.method(revenue_multiple.NAME), effective
    )
    analysis.dilution = dilution.build(profile, analysis.range)
    _requested_gap(analysis, profile)
    analysis.decision_factors = _decision_factors(analysis, profile)
    analysis.key_milestone = _milestone(analysis, profile)
    _confidence(analysis, profile)
    analysis.assumptions = _assumptions(ledger)
    analysis.comparable_note = _comparable_note(profile)
    return analysis


# --- Methods ----------------------------------------------------------------


def _run_methods(profile: CompanyProfile, ledger: Ledger) -> list[MethodResult]:
    """Run every method the stage nominates, in a stable, reportable order."""
    nominated = config.METHOD_WEIGHTS[profile.stage]
    results: list[MethodResult] = []
    for name in RUNNERS:
        if name in nominated:
            results.append(RUNNERS[name](profile, ledger))
        else:
            results.append(
                MethodResult(
                    name=name,
                    exclusion_reason=(
                        f"Not applied at {config.STAGE_LABELS[profile.stage].lower()}: "
                        "section 4 does not nominate it for this stage."
                    ),
                )
            )
    return results


def _weight(analysis: ValuationAnalysis, profile: CompanyProfile) -> None:
    """Renormalise the stage weights across the methods that actually ran.

    A method that excluded itself does not silently hand its weight to a fixed
    successor; the remaining weights scale up in proportion. Two corrections then
    apply. A capped method — one built on stacked assumptions rather than
    evidence — never rises above its nominal stage weight, so the absence of
    comparables cannot promote the most fragile method in the set. And comparable
    financings take a bonus when the comp set is deep enough to earn it.
    """
    nominated = config.METHOD_WEIGHTS[profile.stage]
    included = [method for method in analysis.methods if method.included]
    if not included:
        return

    weights = {method.name: nominated.get(method.name, 0.0) for method in included}
    total = sum(weights.values())
    if total <= 0:
        weights = {name: 1.0 / len(included) for name in weights}
        total = 1.0
    weights = {name: value / total for name, value in weights.items()}
    weights = _apply_caps(weights, nominated)

    comparable = comparable_financing.NAME
    deep = len(profile.usable_comparables) >= config.MIN_COMPARABLES
    if deep and comparable in weights and len(weights) > 1:
        bonus = min(config.COMPARABLE_WEIGHT_BONUS, 1.0 - weights[comparable])
        others = sum(value for name, value in weights.items() if name != comparable)
        if others > 0:
            for name in weights:
                if name != comparable:
                    weights[name] -= bonus * (weights[name] / others)
            weights[comparable] += bonus

    for method in included:
        method.weight = weights.get(method.name, 0.0)


def _apply_caps(weights: dict[str, float], nominated: dict[str, float]) -> dict[str, float]:
    """Hold capped methods at their nominal weight, giving the excess to the rest.

    With nothing else left to give it to — a capped method running alone — the
    cap is dropped rather than leaving the weights short of one.
    """
    capped = {
        name: nominated.get(name, 0.0)
        for name in config.CAPPED_METHODS
        if name in weights and weights[name] > nominated.get(name, 0.0)
    }
    if not capped:
        return weights

    others = {name: value for name, value in weights.items() if name not in capped}
    if not others or sum(others.values()) <= 0:
        return weights

    freed = sum(weights[name] - limit for name, limit in capped.items())
    total_others = sum(others.values())
    adjusted = dict(weights)
    for name, limit in capped.items():
        adjusted[name] = limit
    for name, value in others.items():
        adjusted[name] = value + freed * (value / total_others)
    return adjusted


def _blend(analysis: ValuationAnalysis) -> ValuationRange | None:
    """Weighted average of the method ranges at each of low, base and high."""
    included = [
        method for method in analysis.methods if method.included and method.range is not None
    ]
    if not included:
        return None
    total = sum(method.weight for method in included)
    if total <= 0:
        return None

    def blend(attribute: str) -> float:
        return (
            sum(getattr(method.range, attribute) * method.weight for method in included) / total
        )

    return ValuationRange(blend("low"), blend("base"), blend("high")).ordered()


# --- Section 11: the requested valuation ------------------------------------


def _requested_gap(analysis: ValuationAnalysis, profile: CompanyProfile) -> None:
    """Measure and classify management's ask against the estimated range."""
    requested = profile.round.reference_valuation
    analysis.requested_valuation = requested
    analysis.requested_basis = profile.round.reference_basis

    if requested is None:
        analysis.requested_status = config.INSUFFICIENT
        analysis.requested_statement = (
            "No requested pre-money valuation or valuation cap was disclosed, so no gap to "
            "the estimated range can be measured."
        )
        return

    base = analysis.range.base
    analysis.requested_gap_amount = requested - base
    analysis.requested_gap_percent = norm.safe_div(requested - base, base)
    analysis.requested_status = _classify(requested, analysis.range, analysis.requested_gap_percent)

    gap = analysis.requested_gap_percent
    direction = "above" if (gap or 0) >= 0 else "below"
    analysis.requested_statement = (
        "Based on the supplied operating metrics, financing comparables, and stated "
        f"assumptions, the {analysis.requested_basis} of {fmt.fmt_money(requested)} appears "
        f"approximately {fmt.fmt_pct(abs(gap or 0.0))} {direction} the centre of the "
        f"defensible range ({fmt.fmt_range(analysis.range.low, analysis.range.high)})."
    )


def _classify(requested: float, valuation: ValuationRange, gap: float | None) -> str:
    if requested < valuation.low:
        return config.BELOW_RANGE
    if requested <= valuation.high:
        return config.WITHIN_RANGE
    if (gap or 0.0) > config.MATERIAL_GAP_THRESHOLD:
        return config.MATERIAL_GAP
    return config.ABOVE_RANGE


# --- Section 12: what will decide it ----------------------------------------


def _decision_factors(analysis: ValuationAnalysis, profile: CompanyProfile) -> list[str]:
    """The three to five things most likely to settle whether the ask is accepted.

    Drawn from the largest discounts first — the reasons the estimate sits where
    it does are the reasons an investor will push back — then from the evidence
    that is simply missing.
    """
    factors: list[str] = []

    for item in analysis.discounts()[:3]:
        factors.append(_resolution(item, profile))

    metrics = profile.metrics
    if metrics.countable_revenue and metrics.growth_rate:
        target = metrics.countable_revenue * (1 + metrics.growth_rate)
        factors.append(
            f"Reaching {fmt.fmt_money(target)} {metrics.revenue_label} while holding growth "
            f"near {fmt.fmt_pct(metrics.growth_rate)}"
        )
    elif not metrics.has_meaningful_revenue:
        factors.append("Converting the first customers into contracted, recognised revenue")

    if not profile.usable_comparables:
        factors.append(
            "Producing comparable financings for this sector and stage, which nothing supplied"
        )
    if metrics.nrr is None and metrics.arr is not None:
        factors.append("Evidencing net revenue retention, which the deck does not disclose")

    deduped: list[str] = []
    for factor in factors:
        if factor not in deduped:
            deduped.append(factor)
    return deduped[:5]


def _resolution(item: Adjustment, profile: CompanyProfile) -> str:
    """Turn a discount factor into the thing that would resolve it."""
    return {
        "largest_customer_pct": "Reducing revenue concentration below 25% for the largest customer",
        "revenue": "Producing revenue on a recognised basis rather than a projected one",
        "growth_rate": "Re-accelerating growth above the stage reference rate",
        "gross_margin": "Lifting gross margin toward the sector reference",
        "nrr": "Lifting net revenue retention above 100%",
        "runway_months": "Extending runway so the round is not raised under time pressure",
        "ltv_cac": "Reaching payback economics that justify further sales spend",
        "regulatory_status": "Securing the regulatory clearance the route to market depends on",
        "ip_summary": "Establishing a defensible position through IP, data or exclusivity",
        "team": "Filling the functional gaps in the team before the next milestone",
        "sales_cycle_months": "Shortening the sales cycle or evidencing a repeatable one",
        "stage": "Demonstrating that the current growth rate is durable, not a first-year effect",
    }.get(item.metric, f"Resolving: {item.label}")


def _milestone(analysis: ValuationAnalysis, profile: CompanyProfile) -> str:
    """The single milestone that would most move the valuation, in one sentence."""
    metrics = profile.metrics
    high = analysis.range.high

    if metrics.countable_revenue:
        method = analysis.method(revenue_multiple.NAME)
        implied = norm.safe_div(analysis.range.base, metrics.countable_revenue)
        if method and method.included and implied:
            needed = norm.safe_div(high, implied)
            if needed:
                growth = norm.safe_div(needed, metrics.countable_revenue)
                return (
                    f"Reaching {fmt.fmt_money(needed)} {metrics.revenue_label} — "
                    f"{(growth - 1) * 100:.0f}% above today — at the current "
                    f"{fmt.fmt_multiple(implied)} implied multiple would support the top of the "
                    f"range at {fmt.fmt_money(high)}."
                )

    if analysis.requested_valuation and analysis.range.base:
        return (
            f"Closing the gap to the {analysis.requested_basis} of "
            f"{fmt.fmt_money(analysis.requested_valuation)} requires evidence worth "
            f"{fmt.fmt_money(analysis.requested_valuation - analysis.range.base)} of value: "
            "the milestone that produces it is the one to negotiate against."
        )
    return (
        f"Evidence that moves the range above {fmt.fmt_money(high)} is the milestone worth "
        "negotiating the round against."
    )


# --- Section 13: confidence -------------------------------------------------


def _confidence(analysis: ValuationAnalysis, profile: CompanyProfile) -> None:
    """Score the confidence in the analysis, and say in one sentence why."""
    metrics = profile.metrics
    points = 0
    reasons: list[str] = []

    revenue = metrics.countable_revenue
    if revenue and revenue >= config.MEANINGFUL_REVENUE_FLOOR:
        points += 2
        if revenue >= config.SERIES_A_REVENUE_FLOOR:
            points += 1
        reasons.append(f"{metrics.revenue_label} of {fmt.fmt_money(revenue)}")
    else:
        reasons.append("no countable revenue")

    comparables = len(profile.usable_comparables)
    if comparables >= config.MIN_COMPARABLES:
        points += 2
        reasons.append(f"{comparables} comparable financings")
    elif comparables:
        points += 1
        reasons.append(f"only {comparables} comparable financing(s)")
    else:
        reasons.append("no comparable financings")

    core = [
        metrics.growth_rate,
        metrics.gross_margin,
        metrics.nrr,
        metrics.customer_count,
        metrics.largest_customer_pct,
        metrics.burn,
    ]
    present = sum(1 for value in core if value is not None)
    points += min(present, 3)
    reasons.append(f"{present} of {len(core)} core operating metrics disclosed")

    if profile.round.raise_amount is not None and profile.round.reference_valuation is not None:
        points += 1

    if points >= HIGH_CONFIDENCE_POINTS:
        analysis.confidence = config.HIGH
    elif points >= MEDIUM_CONFIDENCE_POINTS:
        analysis.confidence = config.MEDIUM
    else:
        analysis.confidence = config.LOW

    assumption_share = _assumption_share(analysis)
    if assumption_share > 0.5 and analysis.confidence == config.HIGH:
        # A high score built mostly on stated priors is not high confidence.
        analysis.confidence = config.MEDIUM
        reasons.append("the majority of the blend rests on stated priors")

    analysis.confidence_reason = fmt.titlecase(
        "; ".join(reasons) + f" ({points} of 10 evidence points)."
    )


def _assumption_share(analysis: ValuationAnalysis) -> float:
    """Weight of the blend whose anchor is an analyst assumption rather than evidence."""
    included = [method for method in analysis.methods if method.included]
    total = sum(method.weight for method in included)
    if total <= 0:
        return 1.0
    assumed = sum(
        method.weight
        for method in included
        if config.ASSUMPTION
        in (method.inputs.get("multiple_source"), method.inputs.get("baseline_source"))
        or method.name == venture_method.NAME
    )
    return assumed / total


# --- Reporting helpers ------------------------------------------------------


def _assumptions(ledger: Ledger) -> list[str]:
    """Every analyst assumption, in the form the report and the JSON print it."""
    return [
        f"{entry.metric.replace('_', ' ')}: {_render(entry.value, entry.unit)} — {entry.source}"
        for entry in ledger.assumptions()
    ]


def _render(value: object, unit: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if unit.startswith("$"):
        return fmt.fmt_money(float(value))
    if unit == "fraction":
        return fmt.fmt_pct(float(value))
    if unit.endswith("x") or unit == "x":
        return fmt.fmt_multiple(float(value))
    return fmt.fmt_number(float(value))


def _comparable_note(profile: CompanyProfile) -> str:
    usable = profile.usable_comparables
    if not usable:
        return config.NO_COMPARABLES
    sources = sorted({item.source for item in usable if item.source})
    suffix = f" Source: {'; '.join(sources)}." if sources else ""
    return f"{len(usable)} comparable financings supplied.{suffix}"
