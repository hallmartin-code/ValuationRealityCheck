"""Revenue / ARR multiple.

Pre-money = countable revenue x revenue multiple.

The multiple band comes from the supplied comparables where enough of them
disclose revenue at financing, and from a stated stage prior otherwise. Either
way the band is a *market* anchor: growth, margin and retention do not move it
here. They move the blended baseline once, in `adjustments`, which is what keeps
the section 8 bridge from counting the same strength twice.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile, Ledger
from ..models.valuation import MethodResult
from ..utils import formatting as fmt
from ..utils import normalization as norm

NAME = "revenue_multiple"

# Comparables with a disclosed revenue figure needed before their implied
# multiples are preferred over the stage prior. Two rows is a pair of anecdotes.
MIN_COMPARABLE_MULTIPLES = 3


def run(profile: CompanyProfile, ledger: Ledger) -> MethodResult:
    """Value the company on a revenue multiple, or say why it cannot be done."""
    result = MethodResult(name=NAME)
    metrics = profile.metrics
    revenue = metrics.countable_revenue

    if revenue is None:
        result.exclusion_reason = (
            "No revenue on a countable basis. "
            + (
                f"The disclosed revenue figure is classified {metrics.revenue_basis} "
                "and section 3 forbids treating it as current revenue."
                if metrics.revenue is not None
                else "The company is pre-revenue."
            )
        )
        return result

    if revenue < config.MEANINGFUL_REVENUE_FLOOR:
        result.exclusion_reason = (
            f"{metrics.revenue_label} of {fmt.fmt_money(revenue)} is below the "
            f"{fmt.fmt_money(config.MEANINGFUL_REVENUE_FLOOR)} floor at which a revenue "
            "multiple carries meaning."
        )
        return result

    band, basis, source_type, confidence = multiple_band(profile)
    if band is None:
        result.exclusion_reason = (
            "No revenue multiple could be anchored: neither the supplied comparables nor a "
            "stage prior applies at this stage."
        )
        return result

    low, base, high = band
    ledger.record(
        "revenue_multiple",
        base,
        source_type,
        basis,
        confidence,
        unit="x revenue",
        notes=f"Band {fmt.fmt_multiple(low)}-{fmt.fmt_multiple(high)}.",
    )

    result.included = True
    result.low = revenue * low
    result.base = revenue * base
    result.high = revenue * high
    result.basis = (
        f"{fmt.fmt_multiple(base)} on {metrics.revenue_label} of {fmt.fmt_money(revenue)}"
    )
    result.detail = f"{fmt.fmt_multiple(low)}–{fmt.fmt_multiple(high)} band · {basis}"
    result.inputs = {
        "revenue": revenue,
        "revenue_label": metrics.revenue_label,
        "revenue_basis": metrics.revenue_basis,
        "multiple_low": low,
        "multiple_base": base,
        "multiple_high": high,
        "multiple_source": source_type,
        "multiple_basis": basis,
    }
    return result


def multiple_band(
    profile: CompanyProfile,
) -> tuple[tuple[float, float, float] | None, str, str, str]:
    """Return (band, basis text, source type, confidence) for the revenue multiple.

    Comparables lead when enough of them price revenue. The stage prior is the
    fallback and is always labelled an analyst assumption, never presented as a
    market observation (section 17).
    """
    observed = [
        comparable.implied_multiple
        for comparable in profile.usable_comparables
        if comparable.implied_multiple is not None and comparable.implied_multiple > 0
    ]
    if len(observed) >= MIN_COMPARABLE_MULTIPLES:
        low = norm.percentile(observed, 0.25)
        base = norm.median(observed)
        high = norm.percentile(observed, 0.75)
        if base:
            return (
                (low or base, base, high or base),
                f"25th/50th/75th percentile of {len(observed)} supplied comparable financings",
                config.COMPARABLE,
                config.HIGH if len(observed) >= 5 else config.MEDIUM,
            )

    prior = config.STAGE_REVENUE_MULTIPLES.get(profile.stage)
    if prior is None:
        return None, "", config.ASSUMPTION, config.LOW

    note = f"stage prior for {config.STAGE_LABELS[profile.stage]}"
    if observed:
        note += f"; only {len(observed)} comparable(s) disclosed revenue, too few to anchor a band"
    return prior, note, config.ASSUMPTION, config.LOW


def effective_multiple(result: MethodResult, net_adjustment: float) -> float | None:
    """The multiple the report quotes after the bridge has been applied.

    The page states one multiple, and it has to be the one that reproduces the
    printed base valuation — not the pre-adjustment anchor.
    """
    if not result.included:
        return None
    base = result.inputs.get("multiple_base")
    return None if base is None else float(base) * (1.0 + net_adjustment)
