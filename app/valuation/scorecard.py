"""Scorecard method, for companies with no revenue to multiply.

A baseline pre-money is scaled by six weighted factors scored against par for the
stage. Par is 1.00 and means "what a company at this stage normally shows" — a
factor the deck never addresses stays at par, because reading silence as either a
strength or a weakness would be inventing a company fact (section 17).

The baseline is the median of the supplied comparables where there are enough of
them, and a stated stage prior otherwise. The scores come from the extraction
pass; every number below is computed here.

A deck that evidences none of the six factors still gets an answer: every factor
sits at par, the multiplier is 1.00, and the valuation is the bare baseline. That
is deliberate. Refusing to value the company would leave the investor with no
range at all, where what they actually need to know is that the only thing
supporting the number is a stage prior — which the detail line says outright, the
assumptions list itemises, and the confidence score reports as LOW.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile, Ledger
from ..models.valuation import MethodResult
from ..utils import formatting as fmt
from ..utils import normalization as norm

NAME = "scorecard"

# A one-point score moves a factor this far from par, so +2 is 1.50 and -2 is
# 0.50 before the per-factor band clamps it.
POINT_VALUE = 0.25


def run(profile: CompanyProfile, ledger: Ledger) -> MethodResult:
    """Scale a stage baseline by the weighted quality factors."""
    result = MethodResult(name=NAME, quality_adjusted=True)

    band, basis, source_type, confidence = baseline(profile)
    if band is None:
        result.exclusion_reason = "No baseline pre-money is defined for this stage."
        return result

    factors, multiplier, scored = _factors(profile)
    low, base, high = band
    ledger.record(
        "scorecard_baseline",
        base,
        source_type,
        basis,
        confidence,
        unit="$ pre-money",
    )
    ledger.record(
        "scorecard_multiplier",
        multiplier,
        config.CALCULATED,
        f"weighted across {scored} of {len(config.SCORECARD_WEIGHTS)} factors scored from the deck",
        config.MEDIUM,
        unit="x baseline",
    )

    result.included = True
    result.low, result.base, result.high = low * multiplier, base * multiplier, high * multiplier
    result.basis = f"{fmt.fmt_money(base)} baseline x {multiplier:.2f} quality multiplier"
    result.detail = (
        f"{basis}; {scored} of {len(config.SCORECARD_WEIGHTS)} factors evidenced"
        + (
            " — the valuation is the bare stage prior, unmodified"
            if scored == 0
            else ""
        )
    )
    result.inputs = {
        "baseline_low": low,
        "baseline_base": base,
        "baseline_high": high,
        "baseline_basis": basis,
        "baseline_source": source_type,
        "multiplier": multiplier,
        "factors": factors,
    }
    return result


def baseline(
    profile: CompanyProfile,
) -> tuple[tuple[float, float, float] | None, str, str, str]:
    """The unadjusted pre-money the factors scale.

    Supplied comparables are preferred: a scorecard anchored on real transactions
    is evidence-led, while one anchored on a stage prior is an assumption wearing
    six decimal places.
    """
    usable = profile.usable_comparables
    valuations = [item.pre_money for item in usable if item.pre_money]
    if len(valuations) >= config.MIN_COMPARABLES:
        low = norm.percentile(valuations, 0.25)
        base = norm.median(valuations)
        high = norm.percentile(valuations, 0.75)
        if base:
            return (
                (low or base, base, high or base),
                f"median pre-money of {len(valuations)} supplied comparable financings",
                config.COMPARABLE,
                config.MEDIUM,
            )

    prior = config.STAGE_PREMONEY_BASELINE.get(profile.stage)
    if prior is None:
        return None, "", config.ASSUMPTION, config.LOW
    note = f"stage prior for {config.STAGE_LABELS[profile.stage]}"
    if valuations:
        note += f"; only {len(valuations)} comparable(s) supplied, too few to anchor a baseline"
    return prior, note, config.ASSUMPTION, config.LOW


def _factors(profile: CompanyProfile) -> tuple[list[dict[str, object]], float, int]:
    """Score the six factors and return (detail rows, total multiplier, evidenced count).

    Weights are applied as a weighted sum rather than a product, so one strong
    factor cannot triple the valuation on its own.
    """
    rows: list[dict[str, object]] = []
    total = 0.0
    scored = 0

    for factor, weight in config.SCORECARD_WEIGHTS.items():
        evidenced = profile.quality.evidenced(factor)
        score = (profile.quality.score(factor) or 0) if evidenced else 0
        scored += 1 if evidenced else 0
        multiplier = norm.clamp(1.0 + score * POINT_VALUE, *config.SCORECARD_FACTOR_BAND)
        total += weight * multiplier
        rows.append(
            {
                "factor": factor,
                "weight": weight,
                "evidenced": evidenced,
                "score": score,
                "multiplier": round(multiplier, 3),
                "rationale": profile.quality.score_rationales.get(factor, ""),
            }
        )

    return rows, norm.clamp(total, *config.SCORECARD_TOTAL_BAND), scored
