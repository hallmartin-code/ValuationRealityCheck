"""Operating metrics: revenue, growth, margin, retention, efficiency, market size.

This module owns the deck's numeric groups and the normalisation that turns them
into one set of units — dollars per year, fractions, months. Two rules from the
standard are enforced here rather than downstream:

* MRR is converted to ARR and recorded as calculated, never as disclosed.
* A revenue figure the deck labels projected, pipeline or aspirational is kept,
  labelled, and excluded from every multiple (section 3).
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..models.company import DeckFacts, Ledger, OperatingMetrics
from ..utils import normalization as norm

FIELDS: dict[str, dict[str, str]] = {
    "metrics": {
        "revenue": (
            "Current annual revenue as printed, carrying the table's scale into the figure. "
            "If the deck shows several years, take the most recent ACTUAL year, never a "
            "forecast year. If a five-year model shows a dash or nil for the current year, "
            "the company is pre-revenue: report the dash's year in revenue_label and leave "
            "this field out rather than reaching into a forecast column."
        ),
        "revenue_label": (
            "The exact column heading or words the deck uses for that revenue figure — "
            "'2025A', '2027', 'projected', 'run rate', 'contracted', 'pipeline'. Copy the "
            "label, not the number. ALWAYS fill this when you fill revenue: a bare future "
            "year here is what stops a forecast being valued as revenue earned."
        ),
        "arr": "Annual recurring revenue as printed.",
        "mrr": "Monthly recurring revenue as printed.",
        "growth_rate": "Revenue growth rate, with its period: '94% YoY', '3x year over year'.",
        "gross_margin": "Gross margin percentage.",
        "ebitda": (
            "EBITDA or operating profit/loss for the same period as revenue. Keep the sign: "
            "a loss is negative, and financial tables write it in parentheses."
        ),
        "burn": "Cash burn, with its period: '$310k/month', '$3.7M per year'.",
        "runway": "Runway remaining, e.g. '14 months'.",
        "customer_count": "Number of customers, accounts or logos today.",
        "acv": "Average contract value or average annual revenue per customer.",
        "customer_concentration": (
            "How concentrated revenue is across customers, in the deck's words."
        ),
        "largest_customer_pct": "Percentage of revenue from the single largest customer.",
        "retention": "Customer or logo retention rate.",
        "gross_retention": "Gross revenue retention (GRR).",
        "nrr": "Net revenue retention (NRR) or net dollar retention. Often above 100%.",
        "churn": "Churn rate, with its period if stated.",
        "cac": "Customer acquisition cost.",
        "ltv": "Customer lifetime value.",
        "ltv_cac": "LTV to CAC ratio, e.g. '4.2x'.",
        "sales_cycle": "Length of the sales cycle, e.g. '4 months'.",
        "pipeline": "Sales pipeline value. This is NOT revenue.",
        "customer_growth": "Growth in customer count, with its period.",
    },
    "market": {
        "tam": "Total addressable market as stated, with its unit.",
        "sam": "Serviceable addressable market as stated.",
        "som": "Serviceable obtainable market as stated.",
    },
}

# Which parser reads which field. Keeping this table beside the catalogue is what
# stops a percentage being read as money or a pipeline figure as a count.
_READERS = {
    "revenue": norm.parse_money,
    "arr": norm.parse_money,
    "mrr": norm.parse_money,
    "growth_rate": norm.parse_percent,
    "gross_margin": norm.parse_percent,
    "ebitda": norm.parse_money,
    "customer_count": norm.parse_count,
    "acv": norm.parse_money,
    "largest_customer_pct": norm.parse_percent,
    "retention": norm.parse_retention,
    "gross_retention": norm.parse_retention,
    "nrr": norm.parse_retention,
    "churn": norm.parse_percent,
    "cac": norm.parse_money,
    "ltv": norm.parse_money,
    "ltv_cac": norm.parse_multiple,
    "pipeline": norm.parse_money,
    "customer_growth": norm.parse_percent,
    "tam": norm.parse_money,
    "sam": norm.parse_money,
    "som": norm.parse_money,
}

# The metrics an investor-grade valuation is expected to have, and the label the
# missing-information list prints when one is absent.
EXPECTED = {
    "revenue": "Current revenue or ARR",
    "growth_rate": "Revenue growth rate",
    "gross_margin": "Gross margin",
    "nrr": "Net revenue retention",
    "customer_count": "Customer count",
    "burn": "Cash burn",
    "runway_months": "Runway",
    "largest_customer_pct": "Customer concentration",
}


def build_metrics(
    facts: DeckFacts,
    overrides: dict[str, Any],
    ledger: Ledger,
    as_of_year: int | None = None,
) -> OperatingMetrics:
    """Resolve the operating metrics, recording the source of each one.

    Precedence is enforced by the ledger: every candidate is offered to it and
    the ledger keeps the highest-authority one. The returned object then reads
    its values back out of the ledger, so the numbers on the page and the
    provenance beside them can never disagree.
    """
    for key in _READERS:
        _offer(key, facts, overrides, ledger)

    _offer_burn(facts, overrides, ledger)
    _offer_runway(facts, overrides, ledger)
    _offer_sales_cycle(facts, overrides, ledger)

    metrics = OperatingMetrics(
        revenue=ledger.number("revenue"),
        arr=ledger.number("arr"),
        mrr=ledger.number("mrr"),
        growth_rate=ledger.number("growth_rate"),
        gross_margin=ledger.number("gross_margin"),
        ebitda=ledger.number("ebitda"),
        burn=ledger.number("burn"),
        runway_months=ledger.number("runway_months"),
        customer_count=ledger.number("customer_count"),
        acv=ledger.number("acv"),
        largest_customer_pct=ledger.number("largest_customer_pct"),
        retention=ledger.number("retention"),
        gross_retention=ledger.number("gross_retention"),
        nrr=ledger.number("nrr"),
        churn=ledger.number("churn"),
        cac=ledger.number("cac"),
        ltv=ledger.number("ltv"),
        ltv_cac=ledger.number("ltv_cac"),
        sales_cycle_months=ledger.number("sales_cycle_months"),
        pipeline=ledger.number("pipeline"),
        customer_growth=ledger.number("customer_growth"),
        tam=ledger.number("tam"),
        sam=ledger.number("sam"),
        som=ledger.number("som"),
    )

    _resolve_arr(metrics, facts, overrides, ledger)
    _classify_revenue(metrics, facts, overrides, ledger, as_of_year)
    _derive(metrics, ledger)
    return metrics


# --- Source offers ----------------------------------------------------------


def _offer(key: str, facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger) -> None:
    """Offer the user value and then the deck value for one metric to the ledger."""
    reader = _READERS[key]
    group = "market" if key in FIELDS["market"] else "metrics"

    if key in overrides and overrides[key] is not None:
        value = reader(overrides[key])
        if value is not None:
            ledger.record(key, value, config.USER, "user input", config.HIGH)

    entry = facts.get(group, key)
    if entry.present:
        value = reader(entry.text)
        if value is not None:
            ledger.record(key, value, config.DECK, entry.citation(), config.MEDIUM)


def _offer_burn(facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger) -> None:
    """Burn, annualised. A deck quoting a monthly figure means twelve times that."""
    if overrides.get("burn") is not None:
        value = norm.parse_money(overrides["burn"])
        if value is not None:
            ledger.record("burn", value, config.USER, "user input, annual", config.HIGH)

    entry = facts.get("metrics", "burn")
    if not entry.present:
        return
    value = norm.parse_money(entry.text)
    if value is None:
        return
    monthly = any(
        marker in f"{entry.text} {entry.quote}".lower()
        for marker in ("/mo", "per month", "monthly", "a month", "/month")
    )
    ledger.record(
        "burn",
        abs(value) * (12.0 if monthly else 1.0),
        config.CALCULATED if monthly else config.DECK,
        f"{entry.citation()}, monthly burn x 12" if monthly else entry.citation(),
        config.MEDIUM,
        unit="$/year",
    )


def _offer_runway(facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger) -> None:
    if overrides.get("runway_months") is not None:
        value = norm.parse_months(overrides["runway_months"])
        if value is not None:
            ledger.record("runway_months", value, config.USER, "user input", config.HIGH)
    entry = facts.get("metrics", "runway")
    if entry.present:
        value = norm.parse_months(entry.text)
        if value is not None:
            ledger.record("runway_months", value, config.DECK, entry.citation(), config.MEDIUM)


def _offer_sales_cycle(facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger) -> None:
    if overrides.get("sales_cycle_months") is not None:
        value = norm.parse_months(overrides["sales_cycle_months"])
        if value is not None:
            ledger.record("sales_cycle_months", value, config.USER, "user input", config.HIGH)
    entry = facts.get("metrics", "sales_cycle")
    if entry.present:
        value = norm.parse_months(entry.text)
        if value is not None:
            ledger.record(
                "sales_cycle_months", value, config.DECK, entry.citation(), config.MEDIUM
            )


# --- Derived figures --------------------------------------------------------


def _resolve_arr(
    metrics: OperatingMetrics, facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger
) -> None:
    """Fill ARR from MRR when only MRR was disclosed, and say so."""
    arr, basis = norm.arr_from(metrics.arr, metrics.mrr)
    if arr is None:
        return
    metrics.arr, metrics.arr_basis = arr, basis
    if basis == "MRR x 12":
        source = ledger.get("mrr")
        ledger.record(
            "arr",
            arr,
            config.CALCULATED,
            f"MRR x 12 ({source.citation() if source else 'disclosed MRR'})",
            config.MEDIUM,
            unit="$/year",
        )


def _classify_revenue(
    metrics: OperatingMetrics,
    facts: DeckFacts,
    overrides: dict[str, Any],
    ledger: Ledger,
    as_of_year: int | None = None,
) -> None:
    """Decide what the revenue figure actually is, and drop it from the multiples if needed.

    The label the deck printed beside the number decides this, not the number, and
    `as_of_year` lets a bare year in that label be read against the date of the
    analysis — which is how a column of a five-year model gets caught.
    A user-supplied `revenue_basis` overrides the reading, because a user who
    says "that figure is contracted" outranks the deck's own wording.
    """
    supplied = str(overrides.get("revenue_basis") or "").strip().lower()
    if supplied in norm.REVENUE_BASES:
        metrics.revenue_basis = supplied
        ledger.record("revenue_basis", supplied, config.USER, "user input", config.HIGH)
        return

    label = facts.get("metrics", "revenue_label")
    figure = facts.get("metrics", "revenue")
    basis = norm.classify_revenue_basis(
        label.text, figure.text, figure.quote, as_of_year=as_of_year
    )
    metrics.revenue_basis = basis
    if metrics.revenue is not None:
        ledger.record(
            "revenue_basis",
            basis,
            config.DECK if label.present else config.CALCULATED,
            label.citation() if label.present else "read from the deck's own revenue label",
            config.MEDIUM if label.present else config.LOW,
        )


def _derive(metrics: OperatingMetrics, ledger: Ledger) -> None:
    """Compute the metrics the deck did not state but its own figures imply."""
    if metrics.acv is None and metrics.customer_count:
        revenue = metrics.countable_revenue
        acv = norm.safe_div(revenue, metrics.customer_count)
        if acv:
            metrics.acv = acv
            ledger.record(
                "acv",
                acv,
                config.CALCULATED,
                f"{metrics.revenue_label} / customer count",
                config.MEDIUM,
                unit="$",
            )

    if metrics.ltv_cac is None and metrics.ltv and metrics.cac:
        ratio = norm.safe_div(metrics.ltv, metrics.cac)
        if ratio:
            metrics.ltv_cac = ratio
            ledger.record("ltv_cac", ratio, config.CALCULATED, "LTV / CAC", config.MEDIUM)

    if metrics.runway_months is None and metrics.burn and metrics.burn > 0:
        # Only derivable when cash on hand is known, which the deck rarely states
        # separately; left missing rather than guessed at.
        pass

    if metrics.churn is None and metrics.retention is not None:
        churn = max(0.0, 1.0 - metrics.retention)
        metrics.churn = churn
        ledger.record("churn", churn, config.CALCULATED, "1 - retention", config.MEDIUM)


def missing_metrics(metrics: OperatingMetrics) -> list[str]:
    """The expected metrics that nothing supplied, for the missing-information list."""
    missing: list[str] = []
    for key, label in EXPECTED.items():
        if key == "revenue":
            if metrics.countable_revenue is None:
                missing.append(label)
            continue
        if getattr(metrics, key, None) is None:
            missing.append(label)
    return missing


def revenue_note(metrics: OperatingMetrics) -> str:
    """One line naming what the revenue figure is, when it is not current revenue."""
    if metrics.revenue is None or norm.is_countable(metrics.revenue_basis):
        return ""
    return (
        f"Revenue of {metrics.revenue:,.0f} is classified {metrics.revenue_basis} and is "
        "excluded from every revenue multiple."
    )
