"""Section 9: how the valuation moves when the two assumptions that matter move.

Two axes, chosen by what the company actually is.

* With countable revenue, the answer is revenue and the multiple. Everything else
  in the model is a second-order adjustment to one of those two numbers.
* Without it, the answer is the baseline the methods anchored on and the odds the
  company reaches the milestone that justifies it.

Both grids are built so the centre cell reproduces the base valuation exactly. A
sensitivity table whose middle disagrees with the headline is worse than none.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile
from ..models.valuation import MethodResult, SensitivityTable, ValuationRange
from ..utils import formatting as fmt

# Revenue steps around today's figure, and the label each one carries.
REVENUE_STEPS = (0.75, 1.00, 1.50)

# Probability-of-success steps for a pre-revenue grid, and the probability the
# base case is taken to embed.
PROBABILITY_STEPS = (0.30, 0.50, 0.70)
REFERENCE_PROBABILITY = 0.50


def build(
    profile: CompanyProfile,
    valuation: ValuationRange,
    revenue_method: MethodResult | None,
    net_adjustment: float,
) -> SensitivityTable:
    """Build the grid appropriate to the company's stage."""
    revenue = profile.metrics.countable_revenue
    if revenue and revenue_method is not None and revenue_method.included:
        return _revenue_grid(profile, revenue, revenue_method, net_adjustment, valuation)
    return _milestone_grid(profile, valuation)


def _revenue_grid(
    profile: CompanyProfile,
    revenue: float,
    method: MethodResult,
    net_adjustment: float,
    valuation: ValuationRange,
) -> SensitivityTable:
    """Revenue down the side, implied multiple across the top.

    The centre column is the multiple the base valuation actually implies —
    blended across every method that ran, after the bridge — not the revenue
    method's own anchor. The outer columns keep the anchor band's spread around
    it. That is what makes the centre cell equal the headline base valuation
    rather than merely resemble it.
    """
    implied = valuation.base / revenue
    anchor = float(method.inputs.get("multiple_base") or 0.0)
    low = float(method.inputs.get("multiple_low") or 0.0)
    high = float(method.inputs.get("multiple_high") or 0.0)
    spread_low = (low / anchor) if anchor else 0.75
    spread_high = (high / anchor) if anchor else 1.35
    multiples = [implied * spread_low, implied, implied * spread_high]
    label = profile.metrics.revenue_label

    rows: list[tuple[str, list[str]]] = []
    for step in REVENUE_STEPS:
        level = revenue * step
        rows.append(
            (fmt.fmt_money(level), [fmt.fmt_money(level * multiple) for multiple in multiples])
        )

    return SensitivityTable(
        title=f"{label} x implied revenue multiple",
        row_axis=label,
        column_axis="Multiple",
        column_labels=[fmt.fmt_multiple(multiple) for multiple in multiples],
        rows=rows,
        base_cell=(1, 1),
        note=(
            f"The centre column is the {fmt.fmt_multiple(implied)} multiple implied by the "
            f"base valuation of {fmt.fmt_money(valuation.base)}; the outer columns carry the "
            f"anchor band's spread. Bridge adjustment {fmt.fmt_signed_pct(net_adjustment)}."
        ),
    )


def _milestone_grid(profile: CompanyProfile, valuation: ValuationRange) -> SensitivityTable:
    """Probability of reaching the value-creating milestone, against the anchor band.

    The base case is taken to embed a 50% chance of the milestone landing, so the
    centre cell reproduces it and the other rows read as "if the odds are worse
    or better than the base case assumes".
    """
    anchors = (valuation.low, valuation.base, valuation.high)
    rows: list[tuple[str, list[str]]] = []
    for probability in PROBABILITY_STEPS:
        scale = probability / REFERENCE_PROBABILITY
        rows.append(
            (fmt.fmt_pct(probability), [fmt.fmt_money(anchor * scale) for anchor in anchors])
        )

    milestone = _milestone_name(profile)
    return SensitivityTable(
        title=f"Probability of {milestone} x valuation anchor",
        row_axis=f"P({milestone})",
        column_axis="Anchor",
        column_labels=["Low", "Base", "High"],
        rows=rows,
        base_cell=(1, 1),
        note=(
            f"The base case embeds a {REFERENCE_PROBABILITY:.0%} chance of "
            f"{milestone}; rows scale the anchor in proportion. ANALYST ASSUMPTION."
        ),
    )


def _milestone_name(profile: CompanyProfile) -> str:
    """Name the milestone the pre-revenue case turns on, from what the deck disclosed."""
    quality = profile.quality
    if quality.regulatory_status.strip() and not quality.regulatory_approvals.strip():
        return "regulatory clearance"
    if quality.product_maturity.strip() and profile.metrics.customer_count:
        return "commercial launch"
    if quality.product_maturity.strip():
        return "technical success"
    return "technical success"
