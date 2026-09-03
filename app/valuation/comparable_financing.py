"""Comparable financing analysis: what companies like this actually priced at.

Comparables are supplied, never fetched and never invented. With none, the
method excludes itself and the report prints "Comparable financing data not
supplied." — which is a finding about the evidence, not a gap to be papered over.

This method reads the comp set as a distribution of **pre-money valuations** —
the 25th, 50th and 75th percentiles — and not as a distribution of multiples.
That division of labour matters. `revenue_multiple` already scales the company's
own revenue by a comp-derived multiple; if this method did the same, the two
would be one calculation reported twice, agreeing with themselves at a combined
weight of eighty per cent and collapsing the range to a false precision.

Read separately they answer different questions — "what does this revenue buy at
market multiples" against "what did companies at this stage actually raise at" —
and they diverge exactly when the subject is a different size from its peers,
which is information rather than noise. The size difference is stated in the
method's detail line rather than corrected away.

Stage and geography are applied here as *selection* filters: they decide which
rows are comparable at all. Growth and company quality are deliberately not
applied here; they adjust the blended baseline once, in `adjustments`.
"""

from __future__ import annotations

from .. import config
from ..models.company import Comparable, CompanyProfile, Ledger
from ..models.valuation import MethodResult
from ..utils import formatting as fmt
from ..utils import normalization as norm

NAME = "comparable_financing"

# Below this many rows a filter is dropped rather than applied: filtering three
# comps down to one replaces a thin sample with a single anecdote.
MIN_AFTER_FILTER = 3


def run(profile: CompanyProfile, ledger: Ledger) -> MethodResult:
    """Anchor the valuation on supplied comparable financings."""
    result = MethodResult(name=NAME)
    usable = profile.usable_comparables

    if not usable:
        result.exclusion_reason = config.NO_COMPARABLES
        return result

    selected, filters = select(usable, profile)

    valuations = [comparable.pre_money for comparable in selected if comparable.pre_money]
    low = norm.percentile(valuations, 0.25) or 0.0
    base = norm.median(valuations) or 0.0
    high = norm.percentile(valuations, 0.75) or 0.0
    if not base:
        result.exclusion_reason = "The supplied comparables carry no usable pre-money valuation."
        return result

    result.included = True
    result.low, result.base, result.high = low, base, high
    result.basis = (
        f"median pre-money {fmt.fmt_money(base)} across {len(valuations)} "
        "supplied comparable financings"
    )
    result.inputs |= {
        "reading": "pre-money percentile",
        "pre_money_p25": low,
        "pre_money_median": base,
        "pre_money_p75": high,
        "comparable_count": len(valuations),
    }
    result.detail = _detail(selected, filters) + _size_note(profile, selected)
    result.inputs |= {
        "selected": [comparable.label() for comparable in selected],
        "filters_applied": filters,
    }

    ledger.record(
        "comparable_baseline",
        result.base,
        config.COMPARABLE,
        result.basis,
        config.HIGH if len(selected) >= config.MIN_COMPARABLES else config.MEDIUM,
        unit="$ pre-money",
        notes=_detail(selected, filters),
    )
    return result


def select(
    comparables: list[Comparable], profile: CompanyProfile
) -> tuple[list[Comparable], list[str]]:
    """Narrow the comp set by stage then geography, keeping the sample viable.

    Each filter is applied only if enough rows survive it. The filters that were
    actually applied are returned so the report can say what the median is a
    median *of*.
    """
    applied: list[str] = []
    selected = list(comparables)

    stage = (profile.identity.stage_text or "").strip().lower()
    if stage:
        matched = [item for item in selected if stage in (item.stage or "").lower()]
        if len(matched) >= MIN_AFTER_FILTER:
            selected = matched
            applied.append(f"stage = {profile.identity.stage_text}")

    geography = (profile.identity.geography or "").strip().lower()
    if geography:
        matched = [item for item in selected if geography in (item.geography or "").lower()]
        if len(matched) >= MIN_AFTER_FILTER:
            selected = matched
            applied.append(f"geography = {profile.identity.geography}")

    return selected, applied


def _detail(selected: list[Comparable], filters: list[str]) -> str:
    scope = "; ".join(filters) if filters else "all supplied comparables, unfiltered"
    return f"{len(selected)} transactions ({scope})"


def _size_note(profile: CompanyProfile, selected: list[Comparable]) -> str:
    """How the company's revenue compares with the comp set's.

    A median pre-money is an absolute figure, so it only transfers to the extent
    the companies are the same size. Rather than silently rescaling — which would
    turn this method back into a revenue multiple — the difference is stated and
    left for the reader to weigh against the multiple method beside it.
    """
    revenue = profile.metrics.countable_revenue
    peers = [
        comparable.recurring_revenue
        for comparable in selected
        if comparable.recurring_revenue
    ]
    if revenue is None or not peers:
        return ""
    median = norm.median(peers)
    if not median:
        return ""
    ratio = revenue / median
    if 0.8 <= ratio <= 1.25:
        return f"; comparable revenue base (peer median {fmt.fmt_money(median)})"
    direction = "larger" if ratio > 1 else "smaller"
    return (
        f"; company is {ratio:.1f}x the peer median revenue of "
        f"{fmt.fmt_money(median)}, so the absolute anchor reads {direction}"
    )


def summary_lines(profile: CompanyProfile, limit: int = 3) -> list[str]:
    """Short descriptions of the comparables, for the report and the JSON record."""
    lines: list[str] = []
    for comparable in profile.usable_comparables[:limit]:
        parts = [comparable.label(), f"pre-money {fmt.fmt_money(comparable.pre_money)}"]
        multiple = comparable.implied_multiple
        if multiple:
            parts.append(f"{fmt.fmt_multiple(multiple)} revenue")
        if comparable.source:
            parts.append(comparable.source)
        lines.append(" · ".join(part for part in parts if part))
    return lines
