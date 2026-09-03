"""One deck, one extraction pass.

The three extractor modules each own a field catalogue. This module merges them
into a single schema and a single request, because sending a deck-sized
transcript three times costs three times as much and gives the model three
partial views of one document.

Its contract with the model is narrow on purpose: copy the figure as printed,
name the slide it is on, quote the line it came from, and leave the field out
when the deck does not say. Reading "$4.5M" as 4,500,000 happens later, in
`utils.normalization`, so a misread is a Python bug rather than a prompt failure.
"""

from __future__ import annotations

from typing import Any

from .. import llm
from ..models.company import DeckFacts, DeckValue
from . import company_extractor, financial_extractor, financing_extractor

# The three catalogues, merged. Group order here is the order they appear in the
# prompt, which runs identity -> money -> quality the way a deck itself does.
FIELDS: dict[str, dict[str, str]] = {
    **company_extractor.FIELDS,
    **financing_extractor.FIELDS,
    **financial_extractor.FIELDS,
}
# `quality` is declared on company_extractor but reads best last, after the numbers.
FIELDS = {key: FIELDS[key] for key in ("company", "financing", "metrics", "market", "quality")}

SYSTEM_PROMPT = (
    "You are an extraction engine for TEN Capital. You read investor pitch decks and report "
    "what they say — nothing more. You never estimate, never infer, never convert units, and "
    "never perform arithmetic. If a deck does not state something, you say so by leaving the "
    "field out. You are judged on fidelity to the source, not on completeness."
)

_RULES = """
Rules, in order of importance:

1. `text` holds THE FIGURE ALONE, copied exactly as printed — including the currency symbol,
   the percent sign and the scale word — in the unit the field asks for. The surrounding
   sentence belongs in `quote`, never in `text`.
     good:  text "$2.4M"        quote "ARR grew to $2.4M in Q4, up 94% year over year"
     good:  text "112%"         quote "Net revenue retention 112%"
     bad:   text "ARR $2.4M, growing 94%, NRR 112%"
   Where a slide gives several figures, put the ONE the field asks for in `text`.
   Do not convert, round, normalise or re-scale the figure you pick.
2. Return one entry per field the deck actually states, and OMIT every field it does not.
   A short list is a correct answer. Never fill a gap with an estimate, an industry norm, or a
   figure implied by another slide. Do not emit an entry with empty text.
3. `field` must be one of the listed names exactly. `slide` is the 1-based slide number the
   figure appears on, taken from the "--- Slide N ---" markers. Use 0 only when you genuinely
   cannot tell.
4. `quote` is a short verbatim fragment (under 25 words) from that slide containing the figure,
   so a reader can find it. Do not paraphrase the quote.
5. Do not add up, subtract, multiply or divide anything. If the deck says a number, report that
   number; if it does not, leave the field out. In particular: do not compute ARR from MRR, do
   not compute a growth rate from two years of revenue, and do not compute post-money from
   pre-money plus the raise. Those are done downstream in code.
6. WATCH THE UNITS ON TABLES. Financial tables are routinely denominated in thousands or
   millions, and the scale is stated once in a header — "$000s", "USD millions", "figures in
   thousands" — not on each cell. When you take a figure from such a table, carry the scale
   into `text` yourself: a cell reading "2,430" under a "$000s" header is `text` "$2,430K",
   never "2,430". If you cannot find a scale header, say so in `quote` by including the
   header row you did find. Getting this wrong understates a figure a thousandfold.
7. REVENUE IS THE FIELD MOST OFTEN GOT WRONG. `metrics.revenue` is the most recent ACTUAL
   revenue — a year already closed, or revenue recognised to date. Look for a column marked
   A, actual, or a year at or before the deck's own date.
   If every revenue figure on the deck is a forecast, still report the nearest one, and put
   the deck's exact label for it in `metrics.revenue_label` — "2027E", "projected", "plan" —
   so it can be excluded from the multiples downstream. ALWAYS fill `metrics.revenue_label`
   when you fill `metrics.revenue`; an unlabelled figure is treated as current revenue, which
   is the most expensive mistake this extractor can make.
   A table whose columns are consecutive future years is a financial model, and every
   column of it is a forecast — including the first. Taking a number out of one without
   its column heading is the single most damaging error you can make here.
   Pipeline and signed-LOI value are NOT revenue: they belong in `metrics.pipeline`.
8. For fields that describe rather than measure (business_model, ip_summary, use_of_funds,
   differentiation), a faithful one-or-two-sentence summary in the deck's own vocabulary is fine.
""".strip()

_CONTRADICTION_RULES = """
Report any place the deck contradicts itself on a figure this analysis depends on — two
different ARR numbers, a round size that differs between the cover and the ask slide, a
growth rate inconsistent with the revenue chart beside it. Quote both sides and name both
slides. Do not resolve the contradiction; reporting it is the job.
""".strip()


def field_names() -> list[str]:
    """Every extractable field as "group.key", the enum the response is keyed by."""
    return [f"{group}.{key}" for group, fields in FIELDS.items() for key in fields]


def build_schema() -> dict[str, Any]:
    """Build the response schema.

    Findings come back as a flat list of {field, text, slide, quote} rather than a
    nested object per group. Seventy fields each carrying their own three-key
    object compiles to a grammar that is needlessly large; one object definition
    plus an enum of field names is a fraction of the size. It also makes absence
    explicit — a field the deck does not mention simply has no entry — which is
    exactly the contract this extractor enforces.
    """
    return {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "description": "One entry per field the deck actually states. Omit the rest.",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "enum": field_names()},
                        "text": {"type": "string", "description": "Verbatim from the deck."},
                        "slide": {
                            "type": "integer",
                            "description": "1-based slide number, 0 if unknown.",
                        },
                        "quote": {
                            "type": "string",
                            "description": "Short verbatim supporting fragment.",
                        },
                    },
                    "required": ["field", "text", "slide", "quote"],
                    "additionalProperties": False,
                },
            },
            "scorecard": company_extractor.scorecard_schema(),
            "contradictions": {
                "type": "array",
                "description": "Places the deck contradicts itself. Empty when it does not.",
                "items": {"type": "string"},
            },
        },
        "required": ["facts", "scorecard", "contradictions"],
        "additionalProperties": False,
    }


def build_prompt(deck: dict[str, Any]) -> str:
    """Assemble the extraction turn: rules, catalogue, then the transcript."""
    catalogue = "\n".join(
        f"{group}.{key}: {guidance}"
        for group, fields in FIELDS.items()
        for key, guidance in fields.items()
    )
    return "\n".join(
        [
            "Extract the fields below from the pitch deck transcript at the end of this message.",
            "",
            _RULES,
            "",
            "FIELDS:",
            catalogue,
            "",
            "SCORECARD CLASSIFICATION:",
            company_extractor.scorecard_guidance(),
            "",
            "CONTRADICTIONS:",
            _CONTRADICTION_RULES,
            "",
            f"=== PITCH DECK ({deck['source']}, {deck['slide_count']} slides) ===",
            deck["full_text"],
        ]
    )


def extract(deck: dict[str, Any], client: Any | None = None) -> tuple[DeckFacts, dict[str, Any]]:
    """Extract the deck into DeckFacts plus the scorecard classification."""
    parsed = llm.complete_json(
        SYSTEM_PROMPT,
        build_prompt(deck),
        build_schema(),
        client=client,
        max_tokens=24000,
    )
    return build_facts(parsed, deck), parsed.get("scorecard") or {}


def build_facts(parsed: dict[str, Any], deck: dict[str, Any]) -> DeckFacts:
    """Turn one parsed response into DeckFacts. Split out so it can be tested offline."""
    facts = DeckFacts(slide_count=deck.get("slide_count", 0), source_file=deck.get("source", ""))
    for group, fields in FIELDS.items():
        setattr(facts, group, {key: DeckValue() for key in fields})

    valid = set(field_names())
    for raw in parsed.get("facts") or []:
        name = str(raw.get("field") or "")
        if name not in valid:
            continue
        group, _, key = name.partition(".")
        getattr(facts, group)[key] = _value(raw)

    facts.contradictions = [
        text.strip() for text in (parsed.get("contradictions") or []) if str(text).strip()
    ]
    return facts


_ABSENT = ("n/a", "none", "not stated", "not disclosed", "unknown", "-", "tbd")


def _value(raw: Any) -> DeckValue:
    """Coerce one extracted field into a DeckValue, tolerating a bare string."""
    if isinstance(raw, str):
        return DeckValue(text=raw.strip())
    if not isinstance(raw, dict):
        return DeckValue()
    text = str(raw.get("text") or "").strip()
    if text.lower() in _ABSENT:
        text = ""
    return DeckValue(text=text, slide=_slide(raw.get("slide")), quote=str(raw.get("quote") or ""))


def _slide(raw: Any) -> int | None:
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
