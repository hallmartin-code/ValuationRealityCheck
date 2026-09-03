"""The venture capital method.

    exit value / required return  ->  post-money the round can bear today
    x (1 - dilution to exit)      ->  adjusted for the equity issued after this round
    - the raise                   ->  pre-money

Every input the deck does not supply is stated as an analyst assumption and put
in the ledger. When the method cannot be grounded at all — no revenue and no
serviceable market to grow from — it excludes itself rather than inventing a
terminal value (section 6).
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile, Ledger
from ..models.valuation import MethodResult
from ..utils import formatting as fmt
from ..utils import normalization as norm

NAME = "venture_method"

# Share of the serviceable obtainable market a pre-revenue company is assumed to
# hold at exit, when SOM is the only revenue anchor available. Deliberately
# modest, and always labelled.
SOM_CAPTURE = 0.10

# Growth is not sustained flat for six years. A stated rate is decayed toward
# this floor each year.
TERMINAL_GROWTH_FLOOR = 0.15


def run(profile: CompanyProfile, ledger: Ledger) -> MethodResult:
    """Value the company by discounting a modelled exit back to today."""
    result = MethodResult(name=NAME)
    stage = profile.stage
    raise_amount = profile.round.raise_amount

    if raise_amount is None or raise_amount <= 0:
        result.exclusion_reason = (
            "The VC method prices a round, and no round size was disclosed."
        )
        return result

    exit_revenue, revenue_basis, assumptions = _exit_revenue(profile, stage, ledger)
    if exit_revenue is None:
        result.exclusion_reason = (
            "No revenue and no serviceable obtainable market to grow from, so no terminal "
            "value can be modelled without inventing one."
        )
        return result

    years = config.VC_YEARS_TO_EXIT[stage]
    required_return = config.VC_TARGET_RETURN[stage]
    dilution_to_exit = config.VC_FUTURE_DILUTION[stage]
    ledger.record(
        "vc_years_to_exit",
        years,
        config.ASSUMPTION,
        f"typical hold for {config.STAGE_LABELS[stage]}",
        config.LOW,
        unit="years",
    )
    ledger.record(
        "vc_required_return",
        required_return,
        config.ASSUMPTION,
        f"target gross multiple sought at {config.STAGE_LABELS[stage]}",
        config.LOW,
        unit="x",
    )
    ledger.record(
        "vc_dilution_to_exit",
        dilution_to_exit,
        config.ASSUMPTION,
        "equity issued in later rounds and option pools before exit",
        config.LOW,
        unit="fraction",
    )

    values: list[float] = []
    for multiple in config.VC_EXIT_MULTIPLE:
        exit_value = exit_revenue * multiple
        post_money = (exit_value / required_return) * (1.0 - dilution_to_exit)
        values.append(max(post_money - raise_amount, 0.0))

    low, base, high = values
    if base <= 0:
        result.exclusion_reason = (
            f"The modelled exit cannot support the {fmt.fmt_money(raise_amount)} raise at a "
            f"{required_return:.0f}x required return, so the method implies no positive "
            "pre-money value."
        )
        return result

    result.included = True
    result.low, result.base, result.high = low, base, high
    result.basis = (
        f"{fmt.fmt_money(exit_revenue * config.VC_EXIT_MULTIPLE[1])} exit in {years} years "
        f"at {required_return:.0f}x required return"
    )
    result.detail = (
        f"{fmt.fmt_money(exit_revenue)} exit revenue ({revenue_basis}) x "
        f"{config.VC_EXIT_MULTIPLE[0]:.0f}–{config.VC_EXIT_MULTIPLE[2]:.0f}x, "
        f"less {dilution_to_exit:.0%} dilution to exit"
    )
    result.inputs = {
        "exit_revenue": exit_revenue,
        "exit_revenue_basis": revenue_basis,
        "years_to_exit": years,
        "required_return": required_return,
        "exit_multiple_band": list(config.VC_EXIT_MULTIPLE),
        "dilution_to_exit": dilution_to_exit,
        "raise_amount": raise_amount,
        "assumptions": assumptions,
    }
    return result


def _exit_revenue(
    profile: CompanyProfile, stage: str, ledger: Ledger
) -> tuple[float | None, str, list[str]]:
    """Model revenue in the exit year, from whichever anchor the company has."""
    years = config.VC_YEARS_TO_EXIT[stage]
    revenue = profile.metrics.countable_revenue
    assumptions: list[str] = []

    if revenue is not None and revenue > 0:
        growth = profile.metrics.growth_rate
        if growth is None:
            growth = config.VC_DEFAULT_GROWTH
            assumptions.append(
                f"Growth of {growth:.0%} assumed for the exit model; the deck states none."
            )
            ledger.record(
                "vc_growth_rate",
                growth,
                config.ASSUMPTION,
                "no growth rate disclosed; stage default applied",
                config.LOW,
                unit="per year",
            )
        else:
            ledger.record(
                "vc_growth_rate",
                growth,
                config.CALCULATED,
                "disclosed growth rate, decayed toward a terminal rate",
                config.MEDIUM,
                unit="per year",
            )

        projected = revenue
        rate = growth
        for _ in range(years):
            projected *= 1.0 + rate
            rate = max(rate * config.VC_GROWTH_DECAY, TERMINAL_GROWTH_FLOOR)
        basis = (
            f"{profile.metrics.revenue_label} of {fmt.fmt_money(revenue)} grown {years} years "
            f"from {growth:.0%}, decaying {1 - config.VC_GROWTH_DECAY:.0%} a year"
        )
        return projected, basis, assumptions

    som = profile.metrics.som
    if som is not None and som > 0:
        assumptions.append(
            f"Exit revenue set at {SOM_CAPTURE:.0%} of the stated serviceable obtainable "
            "market; the company has no revenue to grow from."
        )
        ledger.record(
            "vc_som_capture",
            SOM_CAPTURE,
            config.ASSUMPTION,
            "share of the stated SOM held at exit",
            config.LOW,
            unit="fraction",
        )
        return som * SOM_CAPTURE, f"{SOM_CAPTURE:.0%} of stated SOM", assumptions

    return None, "", assumptions
