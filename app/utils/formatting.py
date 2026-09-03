"""Formatting helpers for the one-page report and the console summary.

Everything an investor reads is formatted here, so the page speaks in one
register: money at deck scale, percentages to one place, and a single em dash
wherever a figure was never disclosed.
"""

from __future__ import annotations

from .. import config

DASH = "—"


def fmt_money(value: float | None, dash: str = DASH) -> str:
    """Format a currency amount at investor-deck scale: $1.2B, $47M, $850k."""
    if value is None:
        return dash
    magnitude = abs(value)
    sign = "-" if value < 0 else ""
    if magnitude >= 1e9:
        return f"{sign}${magnitude / 1e9:.1f}B"
    if magnitude >= 1e6:
        scaled = magnitude / 1e6
        return f"{sign}${scaled:.1f}M" if scaled < 100 else f"{sign}${scaled:.0f}M"
    if magnitude >= 1e3:
        return f"{sign}${magnitude / 1e3:.0f}k"
    return f"{sign}${magnitude:,.0f}"


def fmt_money_exact(value: float | None, dash: str = DASH) -> str:
    """Format a currency amount in full, for the JSON summary and console output."""
    return dash if value is None else f"${value:,.0f}"


def fmt_number(value: float | None, dash: str = DASH) -> str:
    """Format a count. Large counts collapse to k/M so table cells stay narrow."""
    if value is None:
        return dash
    magnitude = abs(value)
    if magnitude >= 1e6:
        return f"{value / 1e6:.1f}M"
    if magnitude >= 10_000:
        return f"{value / 1e3:.0f}k"
    if magnitude >= 100:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def fmt_pct(value: float | None, places: int = 0, dash: str = DASH) -> str:
    """Format a fraction as a percentage."""
    if value is None:
        return dash
    return f"{value * 100:.{places}f}%"


def fmt_signed_pct(value: float | None, places: int = 0, dash: str = DASH) -> str:
    """Format a fraction as a signed percentage: +39%, -12%, 0%.

    The sign is the point on a bridge line and on a premium-to-base figure, so a
    zero prints without one rather than as a misleading +0%.
    """
    if value is None:
        return dash
    percent = value * 100
    if abs(percent) < 0.5 * (10 ** -places):
        return f"{0:.{places}f}%"
    return f"{percent:+.{places}f}%"


def fmt_multiple(value: float | None, dash: str = DASH) -> str:
    return dash if value is None else f"{value:.1f}x"


def fmt_range(low: float | None, high: float | None, dash: str = DASH) -> str:
    """A currency range sharing one scale suffix: $14–23M rather than $14M–$23M."""
    if low is None and high is None:
        return dash
    if low is None or high is None:
        return fmt_money(low if low is not None else high, dash)
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(high) >= threshold:
            scaled_low, scaled_high = low / threshold, high / threshold
            if scaled_high < 10:
                return f"${scaled_low:.1f}–{scaled_high:.1f}{suffix}"
            return f"${scaled_low:.0f}–{scaled_high:.0f}{suffix}"
    return f"${low:,.0f}–{high:,.0f}"


def fmt_months(value: float | None, dash: str = DASH) -> str:
    return dash if value is None else f"{value:.0f} mo"


def method_label(name: str) -> str:
    """Human-readable name for a valuation method key."""
    return {
        "revenue_multiple": "Revenue / ARR multiple",
        "comparable_financing": "Comparable financings",
        "venture_method": "VC method",
        "scorecard": "Scorecard",
    }.get(name, name.replace("_", " ").title())


def source_label(source_type: str) -> str:
    return config.SOURCE_LABELS.get(source_type, source_type)


def titlecase(text: str) -> str:
    """Sentence-case a label without flattening acronyms."""
    text = (text or "").strip()
    return text[0].upper() + text[1:] if text else ""


def truncate_words(text: str, limit: int) -> str:
    """Trim to a word budget, ending on a sentence where one is available.

    An investor page that stops mid-clause reads as a rendering failure, so a
    complete sentence inside the budget beats a longer fragment outside it.
    """
    words = (text or "").split()
    if len(words) <= limit:
        return text or ""

    clipped = " ".join(words[:limit])
    if clipped.endswith((".", "?", "!")):
        return clipped
    boundary = max(clipped.rfind(mark) for mark in (". ", "? ", "! "))
    if boundary > 0 and boundary >= len(clipped) * 0.55:
        return clipped[: boundary + 1]
    return clipped.rstrip(",;:") + "…"


def join_facts(*parts: str, separator: str = " | ") -> str:
    """Join the non-empty parts of a header strip."""
    return separator.join(part.strip() for part in parts if part and part.strip())
