"""Reading deck text into numbers, and normalising those numbers into one unit.

The extractor returns deck text verbatim; every reading of that text into a
number happens here, in Python. That is deliberate — it keeps arithmetic and
unit interpretation out of the model, so a misread is traceable to one function
rather than to a prompt.

Section 3 of the standard also lives here: MRR to ARR, monthly to annualised,
"$2.4M" to 2400000.0, and the classification that stops pipeline or projected
revenue from being counted as current revenue.
"""

from __future__ import annotations

import math
import re
from typing import Any

# --- Numbers ----------------------------------------------------------------

_SCALES: dict[str, float] = {
    "trillion": 1e12,
    "billion": 1e9,
    "million": 1e6,
    "thousand": 1e3,
    "mm": 1e6,
    "bn": 1e9,
    "m": 1e6,
    "b": 1e9,
    "k": 1e3,
}

# Longest token first so "billion" wins over "b" and "mm" over "m". The trailing
# `s?\b` is what separates a scale word from an ordinary one: "Millions" scales,
# "months" does not, because "m" is not followed by a word boundary there.
_SCALE = re.compile(r"^\s*(trillion|billion|million|thousand|mm|bn|m|b|k)s?\b", re.IGNORECASE)

_NUMBER_TEXT = r"[-+]?\d[\d,]*\.?\d*"
_NUMBER = re.compile(_NUMBER_TEXT)
_CURRENCY = re.compile(r"[$€£¥]")
_MONEY = re.compile(r"[$€£¥]\s*(" + _NUMBER_TEXT + r")")
_PERCENT = re.compile(r"(" + _NUMBER_TEXT + r")\s*%")
_MULTIPLE = re.compile(r"(" + _NUMBER_TEXT + r")\s*[x×]\b", re.IGNORECASE)

# Financial tables write a negative in parentheses: an EBITDA line reading
# "(1,377)" is a loss of 1,377, and reading it as a profit inverts the company's
# economics. Rewritten to a leading minus before any number is read.
_ACCOUNTING_NEGATIVE = re.compile(
    r"\(\s*([$€£¥]?\s*[\d,]+\.?\d*\s*"
    r"(?:trillion|billion|million|thousand|mm|bn|[mbk])?s?)\s*\)",
    re.IGNORECASE,
)


def _signed(text: str) -> str:
    """Rewrite accounting parentheses as a leading minus, inside the currency symbol.

    The sign has to land *after* the "$", not before it: `parse_money` looks for a
    currency symbol followed by a number, and "-$1.5M" would hand it "$1.5M" with
    the minus stranded outside the match. "$-1.5M" reads correctly.
    """

    def negate(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        currency = ""
        if body and body[0] in "$€£¥":
            currency, body = body[0], body[1:].strip()
        return f"{currency}-{body}"

    return _ACCOUNTING_NEGATIVE.sub(negate, text)


def parse_number(text: Any) -> float | None:
    """Read the first number out of `text`, honouring a k/m/bn suffix or scale word.

    "$4.5M" -> 4500000.0, "1,250 customers" -> 1250.0, "6.4 Millions" -> 6.4e6.
    Returns None when there is nothing to read.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    cleaned = _CURRENCY.sub("", _signed(text))
    match = _NUMBER.search(cleaned)
    return _read(match, cleaned) if match else None


def _read(match: re.Match[str], source: str) -> float | None:
    """Turn one number match into a float, applying any scale word attached to it."""
    try:
        value = float(match.group(match.lastindex or 0).replace(",", ""))
    except (ValueError, IndexError):
        return None
    scale = _SCALE.match(source[match.end() :])
    return value * _SCALES[scale.group(1).lower()] if scale else value


def parse_money(text: Any) -> float | None:
    """Read a currency amount, preferring a figure the text actually marks as money.

    Deck cells routinely read "38 customers, ACV $62,000". Taking the first number
    would give 38; taking the first currency-marked number gives the amount the
    field is asking for. A bare percentage is never money.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    if "%" in text and not _CURRENCY.search(text):
        return None

    text = _signed(text)
    match = _MONEY.search(text)
    if match:
        return _read(match, text)
    return parse_number(text)


def parse_percent(text: Any) -> float | None:
    """Read a percentage as a fraction, preferring a figure marked with a percent sign.

    A bare number is read as a percentage when it is above 1.0 and as an already
    normalised fraction otherwise, so both "94%" and 0.94 arrive as 0.94.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = float(text)
        return value / 100.0 if value > 1.0 else value
    if not isinstance(text, str):
        return None

    match = _PERCENT.search(text)
    if match:
        value = _read(match, text)
        return value / 100.0 if value is not None else None

    number = parse_number(text)
    if number is None:
        return None
    return number / 100.0 if number > 1.0 else number


def parse_retention(text: Any) -> float | None:
    """Read a retention figure, which is routinely quoted above 100%.

    Net revenue retention of 112% must stay 1.12 rather than collapsing to 1.0,
    so the value is only divided when it is plainly a percentage. A bare 1.12 is
    already a fraction; a bare 112 is not.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = float(text)
        return value / 100.0 if value > 3.0 else value
    if not isinstance(text, str):
        return None
    match = _PERCENT.search(text)
    value = _read(match, text) if match else parse_number(text)
    if value is None:
        return None
    return value / 100.0 if value > 3.0 else value


def parse_multiple(text: Any) -> float | None:
    """Read a valuation multiple: "6.0x" -> 6.0, "6x ARR" -> 6.0."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    match = _MULTIPLE.search(text)
    if match:
        return _read(match, text)
    return parse_number(text)


def parse_months(text: Any) -> float | None:
    """Read a duration in months. "18 months" -> 18, "2 years" -> 24."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    number = parse_number(text)
    if number is None:
        return None
    lowered = text.lower()
    if "year" in lowered:
        return number * 12
    if "quarter" in lowered:
        return number * 3
    if "week" in lowered:
        return number / 4.345
    if "day" in lowered:
        return number / 30.44
    return number


def parse_count(text: Any) -> float | None:
    """Read a count, rejecting a figure that is plainly money or a percentage."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    if not isinstance(text, str):
        return None
    if _CURRENCY.search(text) or "%" in text:
        return None
    return parse_number(text)


def parse_bool(text: Any) -> bool | None:
    """Read a yes/no field. Anything unrecognised is unknown, not False."""
    if isinstance(text, bool):
        return text
    if not isinstance(text, str):
        return None
    lowered = text.strip().lower()
    if lowered in ("yes", "true", "y", "granted", "approved", "cleared"):
        return True
    if lowered in ("no", "false", "n", "none", "not yet", "pending"):
        return False
    return None


# --- Recurring revenue ------------------------------------------------------


def annualise(value: float | None, period: str) -> float | None:
    """Scale a figure quoted over `period` up to a year.

    `period` is one of "monthly", "quarterly", "annual". Anything else is
    returned unchanged rather than guessed at.
    """
    if value is None:
        return None
    factor = {"monthly": 12.0, "quarterly": 4.0, "annual": 1.0}.get(period)
    return value * factor if factor else value


def arr_from(arr: float | None, mrr: float | None) -> tuple[float | None, str]:
    """Resolve ARR, deriving it from MRR when only MRR was given.

    Returns (arr, basis) where basis records which input produced the figure, so
    the ledger can say "calculated from MRR" rather than presenting a derived
    number as a disclosed one.
    """
    if arr is not None:
        return arr, "stated ARR"
    if mrr is not None:
        return mrr * 12.0, "MRR x 12"
    return None, ""


# --- Revenue classification -------------------------------------------------
#
# Section 3: never treat pipeline or projected revenue as current revenue. The
# classifier reads the label a deck puts on a revenue figure, not the figure.

CURRENT = "current"
RUN_RATE = "run_rate"
CONTRACTED = "contracted"
HISTORICAL = "historical"
PROJECTED = "projected"
PIPELINE = "pipeline"
ASPIRATIONAL = "aspirational"

REVENUE_BASES = (CURRENT, RUN_RATE, CONTRACTED, HISTORICAL, PROJECTED, PIPELINE, ASPIRATIONAL)

# A basis in this set may be multiplied by a revenue multiple. The rest may not:
# they describe money the company does not have yet.
COUNTABLE_BASES = (CURRENT, RUN_RATE, CONTRACTED, HISTORICAL)

_BASIS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        PIPELINE,
        ("pipeline", "prospect", "opportunit", "lead", "in discussion", "loi",
         "letter of intent"),
    ),
    (
        PROJECTED,
        ("projected", "forecast", "plan", "target", "expected", "budget", "20e", "e)",
         "exit"),
    ),
    (ASPIRATIONAL, ("potential", "could reach", "up to", "at scale", "if we", "aspiration")),
    (CONTRACTED, ("contracted", "signed", "booked", "backlog", "committed", "under contract")),
    (RUN_RATE, ("run rate", "run-rate", "annualized", "annualised", "arr", "exit rate")),
    # No "FY20.." marker here: a fiscal-year label is as often ahead of the deck
    # as behind it, and the year test below settles which.
    (HISTORICAL, ("last year", "trailing", "ttm", "historical", "prior year")),
    (CURRENT, ("current", "today", "actual", "to date", "ytd", "recognized", "recognised")),
)


# Digit boundaries rather than word boundaries: the year in "FY2029" has a letter
# before it, and that label is exactly what this rule exists to catch.
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def classify_revenue_basis(*texts: Any, as_of_year: int | None = None) -> str:
    """Classify what a revenue figure actually is, from the words around it.

    Word markers are checked most-dangerous first: a line reading "2026 projected
    pipeline" is pipeline, not projection, and neither is current revenue.

    When the words settle nothing, the year does. A figure labelled only "2027" in
    a deck written in 2026 is a forecast, and the most common way a projection
    reaches a revenue multiple is a five-year model whose columns are bare years —
    so a label naming a year later than the analysis year is projected, whatever
    else it says. Only with no marker and no future year is the figure treated as
    current, which is what the extractor was asked for in the first place.
    """
    blob = " ".join(str(text or "") for text in texts).lower()
    if not blob.strip():
        return CURRENT

    for basis, markers in _BASIS_MARKERS:
        if any(marker in blob for marker in markers):
            if basis in (PROJECTED, PIPELINE, ASPIRATIONAL):
                return basis
            break

    if as_of_year is not None:
        years = [int(match.group(0)) for match in _YEAR.finditer(blob)]
        if years and max(years) > as_of_year:
            return PROJECTED

    for basis, markers in _BASIS_MARKERS:
        if any(marker in blob for marker in markers):
            return basis
    return CURRENT


def is_countable(basis: str) -> bool:
    """Whether revenue on this basis may be used as the denominator of a multiple."""
    return basis in COUNTABLE_BASES


# --- Financing normalisation ------------------------------------------------


def complete_round(
    pre_money: float | None, post_money: float | None, raise_amount: float | None
) -> tuple[float | None, float | None, float | None]:
    """Fill in whichever of pre / post / raise can be derived from the other two.

    pre + raise = post is the only identity available, so exactly one missing
    value can be recovered. Two missing values stay missing.
    """
    known = sum(value is not None for value in (pre_money, post_money, raise_amount))
    if known < 2:
        return pre_money, post_money, raise_amount
    if post_money is None:
        post_money = pre_money + raise_amount
    elif pre_money is None:
        pre_money = post_money - raise_amount
    elif raise_amount is None:
        raise_amount = post_money - pre_money
    return pre_money, post_money, raise_amount


def infer_stage(stage_text: str, revenue: float | None, raise_amount: float | None) -> str:
    """Resolve the stage band that decides which valuation methods may run.

    Revenue leads: it decides the band outright wherever there is enough of it,
    and the deck's own word only breaks ties between the two bands the revenue
    leaves open.

    With no countable revenue the deck's claim is capped rather than accepted or
    discarded. A company describing itself as Series A with nothing earned is not
    a pre-seed — a clinical-stage therapeutic never has revenue and is not priced
    like a two-person prototype — but neither is it what its label claims, so the
    band tops out at seed and the revenue methods exclude themselves regardless.
    """
    from .. import config

    lowered = (stage_text or "").lower()
    if revenue is not None and revenue >= config.MEANINGFUL_REVENUE_FLOOR:
        if revenue >= config.GROWTH_REVENUE_FLOOR:
            return config.GROWTH
        if revenue >= config.SERIES_A_REVENUE_FLOOR:
            return config.SERIES_A
        # Between the seed and Series A floors the deck's own label breaks the tie.
        if "series a" in lowered or "series b" in lowered:
            return config.SERIES_A
        return config.SEED

    # No countable revenue. The round's own size is the only remaining signal.
    if "series b" in lowered or "growth" in lowered:
        return config.SEED
    if "series a" in lowered:
        return config.SEED
    if raise_amount is not None and raise_amount >= 5_000_000:
        return config.SEED
    return config.PRE_REVENUE


# --- Safe arithmetic --------------------------------------------------------


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    """Divide, returning None rather than raising on a missing or zero divisor."""
    if numerator is None or denominator in (None, 0):
        return None
    try:
        result = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def median(values: list[float]) -> float | None:
    return percentile(values, 0.50)


def percentile(values: list[float], fraction: float) -> float | None:
    """Linear-interpolation percentile over a small sample.

    Written out rather than taken from `statistics` so the 25th and 75th points
    the comparable method quotes are computed the same way at any sample size,
    including the two- and three-item samples a private comp set usually is.
    """
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = clamp(fraction, 0.0, 1.0) * (len(clean) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return clean[lower]
    weight = position - lower
    return clean[lower] * (1 - weight) + clean[upper] * weight
