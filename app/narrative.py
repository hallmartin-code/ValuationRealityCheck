"""The interpretation pass: section 12's decision factors, and the key milestone.

This is the only place a model writes prose, and it writes it *about* numbers
Python has already computed. It is given the finished valuation and asked to
choose and phrase what matters; it is never given the chance to change a figure,
because the schema only accepts sentences and every number it might quote is
already fixed on the page.

The pass is optional. `refine` returns the analysis unchanged when no client is
available, and the deterministic factors and milestone the engine produced stand.
That is what lets the whole application run, and be tested, without an API key.
"""

from __future__ import annotations

from typing import Any

from . import config, llm
from .models.company import CompanyProfile
from .models.valuation import ValuationAnalysis
from .utils import formatting as fmt

SYSTEM_PROMPT = (
    "You are a venture analyst at TEN Capital writing the interpretation lines of a "
    "one-page valuation analysis. The valuation has already been calculated and is not "
    "yours to revise. You select and phrase what matters. You never introduce a figure "
    "that is not in the material you were given, and you never soften or inflate a "
    "conclusion the numbers do not support."
)

_RULES = """
Write for a partner who has thirty seconds and has not seen the deck.

1. Use only the figures given below. Do not compute new ones, do not round differently,
   and do not introduce a number from general knowledge.
2. `decision_factors` are the 3 to 5 things most likely to determine whether investors
   accept the requested valuation. Each is a short phrase, under 14 words, naming a
   specific achievable thing — "reaching $4M ARR", "reducing the largest customer below
   25% of revenue", "securing FDA clearance" — not a category like "traction".
3. `key_milestone` is one sentence naming the single milestone that would most materially
   justify a higher valuation, with the figure that goes with it. Under 34 words.
4. Where the evidence is thin, say so plainly rather than hedging. "No comparable
   financings were supplied" is a better sentence than "comparable data is somewhat
   limited".
5. Do not editorialise about the founders. Do not use the words "exciting", "compelling",
   "poised" or "well-positioned".
""".strip()


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "decision_factors": {
                "type": "array",
                "description": "3 to 5 short phrases, highest stakes first.",
                "items": {"type": "string"},
            },
            "key_milestone": {
                "type": "string",
                "description": "One sentence, under 34 words, with the figure that goes with it.",
            },
        },
        "required": ["decision_factors", "key_milestone"],
        "additionalProperties": False,
    }


def refine(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    client: Any | None = None,
    enabled: bool = True,
) -> ValuationAnalysis:
    """Replace the deterministic factors and milestone with written ones.

    Any failure leaves the deterministic text in place: the interpretation is
    worth having, but not at the cost of the run.
    """
    if not enabled:
        return analysis
    try:
        written = llm.complete_json(
            SYSTEM_PROMPT, build_prompt(profile, analysis), schema(), client=client, max_tokens=2000
        )
    except (llm.CredentialsError, ValueError, RuntimeError):
        return analysis

    factors = [
        str(item).strip()
        for item in (written.get("decision_factors") or [])
        if str(item).strip()
    ]
    if 3 <= len(factors) <= 6:
        analysis.decision_factors = factors[:5]

    milestone = str(written.get("key_milestone") or "").strip()
    if milestone:
        analysis.key_milestone = fmt.truncate_words(milestone, 38)
    return analysis


def build_prompt(profile: CompanyProfile, analysis: ValuationAnalysis) -> str:
    """Everything the interpretation may draw on, and nothing else."""
    metrics = profile.metrics
    lines = [
        _RULES,
        "",
        "COMPANY",
        f"Name: {profile.identity.name}",
        f"Sector: {profile.identity.sector or config.NOT_DISCLOSED}",
        f"Stage band: {config.STAGE_LABELS[profile.stage]}",
        f"Business model: {profile.identity.business_model or config.NOT_DISCLOSED}",
        "",
        "METRICS",
        f"{metrics.revenue_label}: {fmt.fmt_money(metrics.countable_revenue)} "
        f"(basis: {metrics.revenue_basis})",
        f"Growth: {fmt.fmt_pct(metrics.growth_rate)}",
        f"Gross margin: {fmt.fmt_pct(metrics.gross_margin)}",
        f"Net revenue retention: {fmt.fmt_pct(metrics.nrr)}",
        f"Customers: {fmt.fmt_number(metrics.customer_count)}",
        f"Largest customer: {fmt.fmt_pct(metrics.largest_customer_pct)} of revenue",
        f"Runway: {fmt.fmt_months(metrics.runway_months)}",
        "",
        "VALUATION (already calculated — do not revise)",
        f"Range: low {fmt.fmt_money(analysis.range.low)}, base "
        f"{fmt.fmt_money(analysis.range.base)}, high {fmt.fmt_money(analysis.range.high)}",
        f"Requested: {fmt.fmt_money(analysis.requested_valuation)} "
        f"({analysis.requested_basis or 'not disclosed'})",
        f"Gap to base: {fmt.fmt_signed_pct(analysis.requested_gap_percent)} — "
        f"{analysis.requested_status}",
        f"Raise: {fmt.fmt_money(profile.round.raise_amount)} "
        f"({profile.round.instrument or config.NOT_DISCLOSED})",
        f"Confidence: {analysis.confidence}",
        f"Comparables: {analysis.comparable_note}",
        "",
        "METHODS USED",
    ]
    for method in analysis.used_methods:
        lines.append(
            f"- {fmt.method_label(method.name)}: {fmt.fmt_money(method.base)} "
            f"at {fmt.fmt_pct(method.weight)} weight — {method.basis}"
        )

    lines += ["", "PREMIUM FACTORS"]
    lines += [f"- +{fmt.fmt_pct(item.pct)} {item.label}" for item in analysis.premiums()] or [
        "- none evidenced"
    ]
    lines += ["", "DISCOUNT FACTORS"]
    lines += [f"- -{fmt.fmt_pct(item.pct)} {item.label}" for item in analysis.discounts()] or [
        "- none evidenced"
    ]

    if profile.missing_information:
        lines += ["", "NOT DISCLOSED", "; ".join(profile.missing_information)]
    if profile.contradictions:
        lines += ["", "CONTRADICTIONS FOUND", "; ".join(profile.contradictions)]

    lines += [
        "",
        "For reference, the deterministic versions this pass replaces:",
        f"Milestone: {analysis.key_milestone}",
        "Factors: " + "; ".join(analysis.decision_factors),
    ]
    return "\n".join(lines)
