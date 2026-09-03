"""Section 10: what the proposed round costs in ownership.

    post-money        = pre-money + new capital
    investor ownership = new capital / post-money
    existing dilution  = investor ownership

Four cases are priced: the founder's own ask, and each of the low, base and high
estimates. Seeing the same raise at four valuations is the fastest way for a
reader to feel the size of the gap section 11 measures.

A SAFE or convertible note does not price the round today, so its dilution is
modelled at the cap and the case says so rather than implying the conversion is
settled.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile
from ..models.valuation import DilutionCase, ValuationRange
from ..utils import formatting as fmt
from ..utils import normalization as norm

REQUESTED = "Requested"
LOW, BASE, HIGH = "Low", "Base", "High"


def build(profile: CompanyProfile, valuation: ValuationRange) -> list[DilutionCase]:
    """Price the round at the requested valuation and at each estimated case."""
    raise_amount = profile.round.raise_amount
    cases: list[DilutionCase] = []

    requested = profile.round.reference_valuation
    if requested is not None:
        cases.append(
            _case(
                REQUESTED,
                requested,
                raise_amount,
                _requested_note(profile),
            )
        )

    for label, pre_money in ((LOW, valuation.low), (BASE, valuation.base), (HIGH, valuation.high)):
        cases.append(_case(label, pre_money, raise_amount, ""))

    return cases


def _case(
    label: str, pre_money: float | None, raise_amount: float | None, note: str
) -> DilutionCase:
    post_money = None if pre_money is None or raise_amount is None else pre_money + raise_amount
    ownership = norm.safe_div(raise_amount, post_money)
    return DilutionCase(
        label=label,
        pre_money=pre_money,
        post_money=post_money,
        new_investor_pct=ownership,
        note=note,
    )


def _requested_note(profile: CompanyProfile) -> str:
    """Say what the requested case was priced off, when it was not a pre-money."""
    terms = profile.round
    if terms.requested_pre_money is not None:
        return ""
    if terms.is_convertible and terms.valuation_cap is not None:
        discount = (
            f", before the {fmt.fmt_pct(terms.discount_rate)} conversion discount"
            if terms.discount_rate
            else ""
        )
        return (
            f"{terms.instrument or 'Convertible instrument'} priced at the "
            f"{fmt.fmt_money(terms.valuation_cap)} cap{discount}; actual conversion depends on "
            "the priced round that triggers it."
        )
    return "Priced off the stated valuation cap."


def committed_note(profile: CompanyProfile) -> str:
    """One line on how much of the round is already spoken for."""
    terms = profile.round
    if terms.committed is None or terms.raise_amount is None or terms.raise_amount <= 0:
        return ""
    share = norm.safe_div(terms.committed, terms.raise_amount)
    remaining = terms.raise_amount - terms.committed
    return (
        f"{fmt.fmt_money(terms.committed)} of {fmt.fmt_money(terms.raise_amount)} committed "
        f"({fmt.fmt_pct(share)}); {fmt.fmt_money(remaining)} remaining."
    )


def reconciles(case: DilutionCase, tolerance: float = 1e-6) -> bool:
    """Whether pre + raise = post and ownership = raise / post hold for this case.

    Used by the validator rather than by the report: section 19 asks for the
    dilution arithmetic to be checked, not assumed.
    """
    if case.pre_money is None or case.post_money is None or case.new_investor_pct is None:
        return True
    raise_amount = case.post_money - case.pre_money
    if case.post_money <= 0:
        return False
    implied = raise_amount / case.post_money
    return abs(implied - case.new_investor_pct) <= tolerance


def instrument_note(profile: CompanyProfile) -> str:
    """The instrument line printed under the dilution table."""
    terms = profile.round
    if not terms.instrument:
        return config.NOT_DISCLOSED
    parts = [terms.instrument]
    if terms.valuation_cap is not None:
        parts.append(f"{fmt.fmt_money(terms.valuation_cap)} cap")
    if terms.discount_rate is not None:
        parts.append(f"{fmt.fmt_pct(terms.discount_rate)} discount")
    return " · ".join(parts)
