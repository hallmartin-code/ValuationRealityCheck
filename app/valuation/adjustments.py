"""The premium / discount framework, and the valuation bridge it produces.

This is the only place company-specific quality adjusts the valuation. The
methods produce market anchors; the factors here move that anchor once, so the
bridge in section 8 reconciles exactly:

    baseline x (1 + sum of signed factors) = adjusted valuation

Two rules govern what may appear.

* Every factor needs an evidenced trigger. A metric that was never disclosed
  produces no factor in either direction — silence is not a weakness.
* A method that already priced quality into its own answer (the scorecard) is
  held out of the adjustment, so the same strength is never counted twice. The
  share of the blend that the factors actually reach is reported as
  `applied_share`, and the bridge prints the effective percentages.

The continuous drivers — growth, gross margin, net revenue retention — are sized
by formula from `config`. The categorical ones use the three-step scale.

One factor from section 7's list is deliberately absent: "unusually high
requested valuation". Discounting the estimate because management asked high
would push the estimate away from the ask and make the section 11 gap partly
self-inflicted. It is reported by the validator as a finding instead, where it
informs the reader without contaminating the number.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile
from ..models.valuation import (
    Adjustment,
    BridgeLine,
    DISCOUNT,
    MethodResult,
    PREMIUM,
    ValuationRange,
)
from ..utils import formatting as fmt
from ..utils import normalization as norm

STRONG = config.ADJUSTMENT_STEP["strong"]
MODERATE = config.ADJUSTMENT_STEP["moderate"]
MILD = config.ADJUSTMENT_STEP["mild"]


def build(profile: CompanyProfile) -> list[Adjustment]:
    """Every premium and discount the evidence supports, strongest first."""
    found: list[Adjustment] = []
    for collect in (
        _growth,
        _retention,
        _margin,
        _concentration,
        _unit_economics,
        _intellectual_property,
        _proprietary_data,
        _regulatory,
        _team,
        _partnerships,
        _revenue_stage,
        _runway,
        _sales_cycle,
        _defensibility,
    ):
        found.extend(collect(profile))
    return sorted(found, key=lambda item: -abs(item.pct))


# --- Continuous drivers -----------------------------------------------------


def _growth(profile: CompanyProfile) -> list[Adjustment]:
    """Growth against the reference rate, in both directions."""
    growth = profile.metrics.growth_rate
    if growth is None or not profile.metrics.has_meaningful_revenue:
        return []
    delta = growth - config.GROWTH_REFERENCE
    pct = norm.clamp(delta * config.GROWTH_SENSITIVITY, -config.GROWTH_CAP, config.GROWTH_CAP)
    if abs(pct) < 0.01:
        return []
    premium = pct > 0
    return [
        Adjustment(
            label=f"{fmt.fmt_pct(growth)} revenue growth",
            direction=PREMIUM if premium else DISCOUNT,
            pct=abs(pct),
            metric="growth_rate",
            rationale=(
                f"Growing at {fmt.fmt_pct(growth)} against a "
                f"{fmt.fmt_pct(config.GROWTH_REFERENCE)} reference rate for the stage."
            ),
        )
    ]


def _retention(profile: CompanyProfile) -> list[Adjustment]:
    """Net revenue retention, the strongest single quality signal in recurring revenue."""
    nrr = profile.metrics.nrr
    if nrr is None:
        return []
    delta = nrr - config.NRR_REFERENCE
    pct = norm.clamp(delta * config.NRR_SENSITIVITY, -config.NRR_CAP, config.NRR_CAP)
    if abs(pct) < 0.01:
        return []
    premium = pct > 0
    return [
        Adjustment(
            label=f"{fmt.fmt_pct(nrr)} net revenue retention",
            direction=PREMIUM if premium else DISCOUNT,
            pct=abs(pct),
            metric="nrr",
            rationale=(
                f"Net revenue retention of {fmt.fmt_pct(nrr)} "
                + ("expands" if premium else "contracts")
                + " the installed base before any new sales."
            ),
        )
    ]


def _margin(profile: CompanyProfile) -> list[Adjustment]:
    margin = profile.metrics.gross_margin
    if margin is None:
        return []
    delta = margin - config.MARGIN_REFERENCE
    pct = norm.clamp(delta * config.MARGIN_SENSITIVITY, -config.MARGIN_CAP, config.MARGIN_CAP)
    if abs(pct) < 0.01:
        return []
    premium = pct > 0
    return [
        Adjustment(
            label=f"{fmt.fmt_pct(margin)} gross margin",
            direction=PREMIUM if premium else DISCOUNT,
            pct=abs(pct),
            metric="gross_margin",
            rationale=(
                f"Gross margin of {fmt.fmt_pct(margin)} against a "
                f"{fmt.fmt_pct(config.MARGIN_REFERENCE)} reference."
            ),
        )
    ]


# --- Revenue quality --------------------------------------------------------


def _concentration(profile: CompanyProfile) -> list[Adjustment]:
    """Dependence on the single largest customer."""
    share = profile.metrics.largest_customer_pct
    if share is None:
        return []
    for threshold, pct in config.CONCENTRATION_BANDS:
        if share >= threshold:
            return [
                Adjustment(
                    label=f"Largest customer = {fmt.fmt_pct(share)} of revenue",
                    direction=DISCOUNT,
                    pct=pct,
                    metric="largest_customer_pct",
                    rationale=(
                        f"{fmt.fmt_pct(share)} of revenue sits with one customer, so the "
                        "revenue base carries that customer's renewal risk."
                    ),
                )
            ]
    if share <= config.CONCENTRATION_PREMIUM_CEILING and profile.metrics.has_meaningful_revenue:
        return [
            Adjustment(
                label=f"No customer above {fmt.fmt_pct(share)} of revenue",
                direction=PREMIUM,
                pct=MILD,
                metric="largest_customer_pct",
                rationale="Revenue is spread widely enough that no single loss is material.",
            )
        ]
    return []


def _unit_economics(profile: CompanyProfile) -> list[Adjustment]:
    ratio = profile.metrics.ltv_cac
    if ratio is None:
        return []
    if ratio >= 3.0:
        return [
            Adjustment(
                label=f"LTV/CAC of {fmt.fmt_multiple(ratio)}",
                direction=PREMIUM,
                pct=MILD,
                metric="ltv_cac",
                rationale="Acquisition pays back several times over, so growth compounds equity.",
            )
        ]
    if ratio < 1.5:
        return [
            Adjustment(
                label=f"LTV/CAC of {fmt.fmt_multiple(ratio)}",
                direction=DISCOUNT,
                pct=MODERATE,
                metric="ltv_cac",
                rationale="Each new customer returns little more than it costs to win.",
            )
        ]
    return []


# --- Defensibility ----------------------------------------------------------


def _intellectual_property(profile: CompanyProfile) -> list[Adjustment]:
    quality = profile.quality
    granted = quality.patents_granted or 0
    filed = quality.patents_filed or 0
    if granted >= 1:
        # A single granted patent is a fence, not an estate. A portfolio of three
        # or more is what starts to look like a durable position.
        return [
            Adjustment(
                label=f"{granted:.0f} granted patent{'s' if granted != 1 else ''}",
                direction=PREMIUM,
                pct=MODERATE if granted >= 3 else MILD,
                metric="patents_granted",
                rationale="Granted claims are enforceable now, not an application under review.",
            )
        ]
    if filed >= 1:
        return [
            Adjustment(
                label=f"{filed:.0f} patent application{'s' if filed != 1 else ''} filed",
                direction=PREMIUM,
                pct=MILD,
                metric="patents_filed",
                rationale="Filings establish priority but confer no enforceable right yet.",
            )
        ]
    return []


def _proprietary_data(profile: CompanyProfile) -> list[Adjustment]:
    if not profile.quality.proprietary_data.strip():
        return []
    return [
        Adjustment(
            label="Proprietary data asset",
            direction=PREMIUM,
            pct=MILD,
            metric="proprietary_data",
            rationale=fmt.truncate_words(profile.quality.proprietary_data, 18),
        )
    ]


def _regulatory(profile: CompanyProfile) -> list[Adjustment]:
    """Approval held is a premium; an approval still required is a discount."""
    quality = profile.quality
    if quality.regulatory_approvals.strip():
        return [
            Adjustment(
                label="Regulatory approval held",
                direction=PREMIUM,
                pct=MODERATE,
                metric="regulatory_approvals",
                rationale=fmt.truncate_words(quality.regulatory_approvals, 18),
            )
        ]
    status = quality.regulatory_status.strip()
    if status:
        return [
            Adjustment(
                label="Approval still required",
                direction=DISCOUNT,
                pct=MODERATE,
                metric="regulatory_status",
                rationale=(
                    "The route to market runs through an approval the company does not yet "
                    f"hold: {fmt.truncate_words(status, 14)}"
                ),
            )
        ]
    return []


def _defensibility(profile: CompanyProfile) -> list[Adjustment]:
    """No disclosed moat of any kind, in a deck that had every chance to claim one."""
    quality = profile.quality
    claimed = any(
        (
            quality.ip_summary.strip(),
            quality.patents_filed,
            quality.patents_granted,
            quality.proprietary_technology.strip(),
            quality.proprietary_data.strip(),
            quality.regulatory_approvals.strip(),
        )
    )
    if claimed:
        return []
    return [
        Adjustment(
            label="No defensibility disclosed",
            direction=DISCOUNT,
            pct=MILD,
            metric="ip_summary",
            rationale=(
                "The deck claims no patents, proprietary technology, data or regulatory "
                "exclusivity, so nothing on the page raises the cost of imitation."
            ),
        )
    ]


# --- Team, partners, stage --------------------------------------------------


def _team(profile: CompanyProfile) -> list[Adjustment]:
    """Founder and management strength, from the scorecard classification."""
    if not profile.quality.evidenced("team"):
        return []
    score = profile.quality.score("team") or 0
    if score >= 2:
        pct, direction = MODERATE, PREMIUM
    elif score == 1:
        pct, direction = MILD, PREMIUM
    elif score == -1:
        pct, direction = MILD, DISCOUNT
    elif score <= -2:
        pct, direction = MODERATE, DISCOUNT
    else:
        return []
    return [
        Adjustment(
            label="Founder and team strength" if score > 0 else "Team gaps",
            direction=direction,
            pct=pct,
            metric="team",
            rationale=fmt.truncate_words(
                profile.quality.score_rationales.get("team", ""), 20
            )
            or "Scored against what this stage normally shows.",
        )
    ]


def _partnerships(profile: CompanyProfile) -> list[Adjustment]:
    if not profile.quality.strategic_partnerships.strip():
        return []
    return [
        Adjustment(
            label="Named strategic partnerships",
            direction=PREMIUM,
            pct=MILD,
            metric="strategic_partnerships",
            rationale=fmt.truncate_words(profile.quality.strategic_partnerships, 18),
        )
    ]


def _revenue_stage(profile: CompanyProfile) -> list[Adjustment]:
    """Pre-revenue status, and the execution risk of a short operating history."""
    found: list[Adjustment] = []
    if not profile.metrics.has_meaningful_revenue:
        countable = profile.metrics.countable_revenue
        # There is a difference between no revenue at all and a few thousand
        # dollars of it, and the rationale should not claim the first when the
        # snapshot above it prints the second.
        detail = (
            f"Revenue of {fmt.fmt_money(countable)} is below the level at which a multiple "
            "carries meaning"
            if countable
            else "No countable revenue"
        )
        found.append(
            Adjustment(
                label="Pre-revenue" if not countable else "Revenue below a meaningful level",
                direction=DISCOUNT,
                pct=MODERATE,
                metric="revenue",
                rationale=(
                    f"{detail}, so the valuation rests on the plan rather than on "
                    "demonstrated demand."
                ),
            )
        )
    elif profile.stage in (config.SEED,):
        found.append(
            Adjustment(
                label="Short operating history",
                direction=DISCOUNT,
                pct=MILD,
                metric="stage",
                rationale=(
                    "Revenue is early enough that the durability of the growth rate is "
                    "not yet demonstrated."
                ),
            )
        )
    return found


def _runway(profile: CompanyProfile) -> list[Adjustment]:
    runway = profile.metrics.runway_months
    if runway is None:
        return []
    if runway < 9:
        pct = MODERATE
    elif runway < 12:
        pct = MILD
    else:
        return []
    return [
        Adjustment(
            label=f"{runway:.0f} months of runway",
            direction=DISCOUNT,
            pct=pct,
            metric="runway_months",
            rationale="A short runway weakens the company's position in this negotiation.",
        )
    ]


def _sales_cycle(profile: CompanyProfile) -> list[Adjustment]:
    cycle = profile.metrics.sales_cycle_months
    if cycle is None or cycle < 9:
        return []
    return [
        Adjustment(
            label=f"{cycle:.0f}-month sales cycle",
            direction=DISCOUNT,
            pct=MILD,
            metric="sales_cycle_months",
            rationale="A long cycle slows the conversion of pipeline into revenue.",
        )
    ]


# --- Applying the factors ---------------------------------------------------


def net_adjustment(adjustments: list[Adjustment]) -> float:
    """The signed sum of the factors, clamped into the configured band."""
    total = sum(item.signed_pct for item in adjustments)
    return norm.clamp(total, *config.NET_ADJUSTMENT_BAND)


def applied_share(methods: list[MethodResult]) -> float:
    """The weight of the blend the factors may touch.

    A quality-adjusted method has already priced these strengths in, so its share
    of the weighted baseline is held out. With every method quality-adjusted the
    share is zero and the bridge correctly shows no movement.
    """
    included = [method for method in methods if method.included and method.weight > 0]
    total = sum(method.weight for method in included)
    if total <= 0:
        return 0.0
    open_weight = sum(
        method.weight for method in included if not method.quality_adjusted
    )
    return open_weight / total


def apply(
    baseline: ValuationRange, net: float, share: float
) -> tuple[ValuationRange, float]:
    """Scale the baseline by the factors, returning (range, effective net)."""
    effective = net * share
    return baseline.scaled(1.0 + effective), effective


def build_bridge(
    baseline: float,
    adjustments: list[Adjustment],
    share: float,
    baseline_label: str = "Weighted method baseline",
) -> list[BridgeLine]:
    """The section 8 bridge: baseline, each factor, adjusted total.

    Percentages printed are *effective* — the nominal factor scaled by the share
    of the blend it reaches, and by the clamp if the net hit the configured band.
    That scaling is what lets the printed lines add up to the printed total
    rather than approximately to it. Only factors that move the number by at
    least half a point are printed; the rest stay in the JSON record.
    """
    raw = sum(item.signed_pct for item in adjustments)
    net = net_adjustment(adjustments)
    # When the clamp bites, every factor is scaled down in proportion so the
    # bridge still reconciles. Without this the lines would sum past the total.
    clamp_scale = (net / raw) if raw and abs(raw - net) > 1e-9 else 1.0

    lines = [BridgeLine(label=baseline_label, pct=None, value=baseline)]
    running = baseline
    printed = 0.0
    for item in adjustments:
        effective = item.signed_pct * clamp_scale * share
        if abs(effective) < 0.005:
            continue
        printed += effective
        running += baseline * effective
        lines.append(BridgeLine(label=item.label, pct=effective, value=running))

    total = baseline * (1.0 + net * share)
    rounding = total - running
    if abs(rounding) > 0.005 * max(abs(baseline), 1.0):
        lines.append(
            BridgeLine(
                label="Factors below the reporting threshold",
                pct=net * share - printed,
                value=total,
            )
        )
    lines.append(
        BridgeLine(label="Adjusted base valuation", pct=None, value=total, is_total=True)
    )
    return lines
