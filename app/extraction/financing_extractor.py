"""The proposed round, and the externally supplied comparable financings.

Two jobs, both about money being asked for rather than money already earned.

`build_round` normalises the round to pre / post / raise, completes whichever of
the three is derivable, and flags the case where the deck states all three and
they do not reconcile — section 19 requires a contradiction to be reported, not
silently resolved.

`load_comparables` reads comparable financings from a user-supplied file. It
never invents one. An empty list is a correct answer and the report says so.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .. import config
from ..models.company import Comparable, DeckFacts, Ledger, RoundTerms
from ..utils import normalization as norm

FIELDS: dict[str, dict[str, str]] = {
    "financing": {
        "raise_amount": "Size of the round currently being raised.",
        "instrument": "Security offered: priced equity, SAFE, convertible note.",
        "pre_money": "Pre-money valuation being asked for, if stated.",
        "post_money": "Post-money valuation being asked for, if stated.",
        "valuation_cap": "SAFE or convertible note valuation cap.",
        "discount_rate": "Conversion discount, e.g. '20%'.",
        "interest_rate": "Note interest rate.",
        "maturity": "Note maturity date or term.",
        "committed": "Amount of the current round already committed or soft-circled.",
        "prior_raised": "Total capital raised to date, before this round.",
        "lead_investor": "Named lead investor for the current round.",
        "use_of_funds": "Use of proceeds, with line items if the deck breaks them out.",
    }
}

_READERS = {
    "raise_amount": norm.parse_money,
    "pre_money": norm.parse_money,
    "post_money": norm.parse_money,
    "valuation_cap": norm.parse_money,
    "discount_rate": norm.parse_percent,
    "committed": norm.parse_money,
    "prior_raised": norm.parse_money,
}

# Ledger metric names differ from deck field names where the standard's own
# vocabulary differs: the deck says "pre_money", the record says what was
# *requested*.
_LEDGER_NAMES = {"pre_money": "requested_pre_money", "post_money": "requested_post_money"}

# Reconciliation tolerance for pre + raise = post, as a fraction of post-money.
# Rounding a deck's own figures to one decimal place is not a contradiction.
_RECONCILE_TOLERANCE = 0.02


def build_round(
    facts: DeckFacts, overrides: dict[str, Any], ledger: Ledger
) -> tuple[RoundTerms, list[str]]:
    """Resolve the proposed round. Returns (terms, contradictions)."""
    for key, reader in _READERS.items():
        name = _LEDGER_NAMES.get(key, key)
        supplied = overrides.get(name, overrides.get(key))
        if supplied is not None:
            value = reader(supplied)
            if value is not None:
                ledger.record(name, value, config.USER, "user input", config.HIGH)
        entry = facts.get("financing", key)
        if entry.present:
            value = reader(entry.text)
            if value is not None:
                ledger.record(name, value, config.DECK, entry.citation(), config.MEDIUM)

    instrument = str(overrides.get("instrument") or "").strip() or facts.text(
        "financing", "instrument"
    )
    if instrument:
        entry = facts.get("financing", "instrument")
        ledger.record(
            "instrument",
            instrument,
            config.USER if overrides.get("instrument") else config.DECK,
            "user input" if overrides.get("instrument") else entry.citation(),
            config.HIGH if overrides.get("instrument") else config.MEDIUM,
        )

    terms = RoundTerms(
        raise_amount=ledger.number("raise_amount"),
        instrument=instrument,
        requested_pre_money=ledger.number("requested_pre_money"),
        requested_post_money=ledger.number("requested_post_money"),
        valuation_cap=ledger.number("valuation_cap"),
        discount_rate=ledger.number("discount_rate"),
        committed=ledger.number("committed"),
        prior_raised=ledger.number("prior_raised"),
    )

    contradictions = _reconcile(terms, ledger)
    return terms, contradictions


def _reconcile(terms: RoundTerms, ledger: Ledger) -> list[str]:
    """Check pre + raise = post, then complete whichever value is derivable."""
    contradictions: list[str] = []
    pre, post, raised = terms.requested_pre_money, terms.requested_post_money, terms.raise_amount

    if pre is not None and post is not None and raised is not None:
        implied = pre + raised
        if post and abs(implied - post) / abs(post) > _RECONCILE_TOLERANCE:
            contradictions.append(
                f"The deck states pre-money {pre:,.0f}, raise {raised:,.0f} and post-money "
                f"{post:,.0f}; pre + raise is {implied:,.0f}, which does not reconcile. "
                "The stated pre-money is used and the discrepancy is flagged."
            )
        return contradictions

    filled_pre, filled_post, filled_raise = norm.complete_round(pre, post, raised)
    if pre is None and filled_pre is not None:
        terms.requested_pre_money = filled_pre
        ledger.record(
            "requested_pre_money",
            filled_pre,
            config.CALCULATED,
            "post-money - raise",
            config.MEDIUM,
            unit="$",
        )
    if post is None and filled_post is not None:
        terms.requested_post_money = filled_post
        ledger.record(
            "requested_post_money",
            filled_post,
            config.CALCULATED,
            "pre-money + raise",
            config.MEDIUM,
            unit="$",
        )
    if raised is None and filled_raise is not None:
        terms.raise_amount = filled_raise
        ledger.record(
            "raise_amount",
            filled_raise,
            config.CALCULATED,
            "post-money - pre-money",
            config.MEDIUM,
            unit="$",
        )
    return contradictions


def missing_round_fields(terms: RoundTerms) -> list[str]:
    """The financing inputs the analysis needs and did not get."""
    missing: list[str] = []
    if terms.raise_amount is None:
        missing.append("Current round size")
    if not terms.instrument:
        missing.append("Security / instrument")
    if terms.reference_valuation is None:
        missing.append("Requested pre-money valuation or valuation cap")
    if terms.is_convertible and terms.discount_rate is None:
        missing.append("Conversion discount")
    return missing


# --- Comparable financings --------------------------------------------------

COMPARABLE_FIELDS = (
    "company",
    "sector",
    "stage",
    "financing_date",
    "round_size",
    "pre_money",
    "post_money",
    "revenue",
    "arr",
    "growth",
    "geography",
    "lead_investor",
    "source",
    "source_date",
)

_COMPARABLE_READERS = {
    "round_size": norm.parse_money,
    "pre_money": norm.parse_money,
    "post_money": norm.parse_money,
    "revenue": norm.parse_money,
    "arr": norm.parse_money,
    "growth": norm.parse_percent,
}

# Aliases a hand-built comp sheet is likely to use for the same column.
_ALIASES = {
    "comparable_company": "company",
    "name": "company",
    "date": "financing_date",
    "round": "round_size",
    "round_amount": "round_size",
    "pre": "pre_money",
    "pre_money_valuation": "pre_money",
    "post": "post_money",
    "post_money_valuation": "post_money",
    "revenue_at_financing": "revenue",
    "arr_at_financing": "arr",
    "growth_at_financing": "growth",
    "lead": "lead_investor",
}


def _column(key: Any) -> str:
    """Reduce a spreadsheet heading to a field name.

    A comp sheet is usually built by hand, so its headings are prose: "Pre-money
    valuation", "Comparable Company", "ARR at financing". Spaces, hyphens and
    case are all noise; the alias table then handles the genuinely different
    names for the same column.
    """
    name = "_".join(str(key).strip().lower().replace("-", " ").replace("/", " ").split())
    return _ALIASES.get(name, name)


def load_comparables(path: str | Path | None) -> list[Comparable]:
    """Read comparable financings from a JSON or CSV file.

    Nothing is fabricated here and nothing is fetched: a comparable exists only
    because a file said so. A file with no usable rows returns an empty list,
    and the report prints "Comparable financing data not supplied."
    """
    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No comparables file at {path}")

    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("comparables", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError(
                f"{path.name}: expected a list of comparables, or an object with a "
                "'comparables' key holding one."
            )

    return [built for built in (parse_comparable(row) for row in rows) if built is not None]


def parse_comparable(row: Any) -> Comparable | None:
    """Build one Comparable from a raw mapping, or None when it holds nothing usable."""
    if not isinstance(row, dict):
        return None
    # A hand-edited CSV keeps its notes as leading-# lines, which csv.DictReader
    # hands over as ordinary rows. Treat them as the comments they are.
    first = next(iter(row.values()), "")
    if isinstance(first, str) and first.lstrip().startswith("#"):
        return None

    normalised: dict[str, Any] = {}
    for key, value in row.items():
        name = _column(key)
        if name in COMPARABLE_FIELDS:
            normalised[name] = value

    built: dict[str, Any] = {}
    for name in COMPARABLE_FIELDS:
        raw = normalised.get(name)
        reader = _COMPARABLE_READERS.get(name)
        if reader is not None:
            built[name] = reader(raw) if raw not in (None, "") else None
        else:
            built[name] = str(raw).strip() if raw is not None else ""

    comparable = Comparable(**built)
    # A row that names nobody and prices nothing is a blank line, not a comp.
    if not comparable.company and not comparable.usable:
        return None
    return comparable


def record_comparables(comparables: list[Comparable], ledger: Ledger) -> None:
    """Record the comp set itself as an input, so the audit trail names its source."""
    usable = [item for item in comparables if item.usable]
    if not comparables:
        ledger.record(
            "comparable_financings",
            0,
            config.ASSUMPTION,
            config.NO_COMPARABLES,
            config.LOW,
            notes="No comparable transactions were supplied to this run.",
        )
        return
    sources = sorted({item.source for item in comparables if item.source})
    ledger.record(
        "comparable_financings",
        len(usable),
        config.COMPARABLE,
        "; ".join(sources) or "supplied comparables file",
        config.HIGH if len(usable) >= config.MIN_COMPARABLES else config.MEDIUM,
        notes=f"{len(usable)} of {len(comparables)} supplied rows carry a pre-money valuation.",
    )
