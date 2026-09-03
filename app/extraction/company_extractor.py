"""Company identity and company-quality factors.

This module owns two of the deck's field groups — who the company is, and the
qualitative factors section 1 asks for — plus the scorecard classification that
turns those qualitative fields into the six factors the pre-revenue methods
weigh.

The classification is judgement and is done by the model. The arithmetic that
consumes it is not, and lives in `valuation/scorecard.py`.
"""

from __future__ import annotations

from typing import Any

from .. import config
from ..models.company import DeckFacts, Identity, QualityFactors, UserContext
from ..utils import normalization as norm

# --- Field catalogue --------------------------------------------------------
#
# The guidance text becomes the schema description, so this table is
# simultaneously the prompt, the schema and the documentation of what the
# extractor is expected to find.

FIELDS: dict[str, dict[str, str]] = {
    "company": {
        "name": "Company name exactly as written on the deck.",
        "website": "Company website or domain.",
        "headquarters": "Headquarters city, state or country.",
        "geography": "Primary market geography the company sells into.",
        "sector": "Sector, e.g. 'B2B SaaS', 'medical device', 'fintech', 'industrials'.",
        "subsector": "Narrower category within the sector, if the deck names one.",
        "business_model": (
            "How revenue is earned: subscription, licence, per-unit, transaction fee."
        ),
        "stage": "Funding or development stage as the deck describes it: pre-seed, seed, Series A.",
    },
    "quality": {
        "ip_summary": (
            "Intellectual property position as described: patents, trade secrets, licences."
        ),
        "patents_filed": "Number of patents or applications FILED. The count alone.",
        "patents_granted": "Number of patents GRANTED or issued. The count alone.",
        "proprietary_technology": "Proprietary technology or technical moat the deck claims.",
        "proprietary_data": "Proprietary or exclusive data assets the deck claims.",
        "regulatory_approvals": "Regulatory clearances or approvals already held.",
        "regulatory_status": "Regulatory pathway and where the company currently sits on it.",
        "product_maturity": "Product stage: concept, prototype, beta, in market, scaled.",
        "differentiation": "Competitive differentiation the deck claims, in its own words.",
        "founder_experience": "Founder background and prior operating experience.",
        "management_quality": "Management team composition, seniority and functional coverage.",
        "relevant_exits": "Prior exits, IPOs or acquisitions by the founders or team.",
        "domain_expertise": "Depth of domain expertise the team brings to this market.",
        "customer_validation": (
            "Evidence customers want this: pilots, references, endorsements, LOIs."
        ),
        "strategic_partnerships": (
            "Named strategic partners, channel partners or distribution deals."
        ),
    },
}

# --- Scorecard classification ----------------------------------------------

SCORECARD_FACTORS: dict[str, str] = {
    "team": (
        "Founder and management strength relative to what this stage and sector normally has: "
        "domain depth, prior exits, functional coverage, full-time commitment."
    ),
    "opportunity": (
        "Size and quality of the market opportunity as evidenced in the deck: addressable "
        "market with a stated methodology, urgency, buyer clarity."
    ),
    "product": (
        "Product and technology maturity: how far past concept the thing actually is, and "
        "whether the deck shows it working."
    ),
    "defensibility": (
        "Durability of the position: granted IP, regulatory exclusivity, proprietary data, "
        "switching costs, network effects."
    ),
    "traction": (
        "Evidence the market is responding: revenue, customers, pilots converting, "
        "non-dilutive funding, retention."
    ),
    "partnerships": (
        "Strategic partnerships, channel relationships or distribution agreements that "
        "materially change the go-to-market."
    ),
}


def scorecard_schema() -> dict[str, Any]:
    """Schema fragment for the six scorecard factors.

    `evidence` is the guard against inventing strength. A factor the deck never
    addresses must come back "absent", and the scorecard then holds it at neutral
    rather than reading silence as either a strength or a weakness (section 17).
    """
    return {
        "type": "object",
        "description": (
            "Score each factor against what a company at THIS stage in THIS sector normally "
            "shows. 0 means par or not addressed. Positive means better than par with "
            "evidence on the slides. Negative means visibly weaker than par."
        ),
        "properties": {
            factor: {
                "type": "object",
                "properties": {
                    "evidence": {
                        "type": "string",
                        "enum": ["present", "absent"],
                        "description": "'absent' if the deck does not address this factor at all.",
                    },
                    "score": {
                        "type": "integer",
                        "description": (
                            "-2 materially weaker than par, -1 weaker, 0 par or not addressed, "
                            "+1 stronger, +2 materially stronger. Must be 0 when evidence "
                            "is absent."
                        ),
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One sentence citing the slide that justifies the score.",
                    },
                },
                "required": ["evidence", "score", "rationale"],
                "additionalProperties": False,
            }
            for factor in SCORECARD_FACTORS
        },
        "required": list(SCORECARD_FACTORS),
        "additionalProperties": False,
    }


def scorecard_guidance() -> str:
    return "\n".join(f"{name}: {guidance}" for name, guidance in SCORECARD_FACTORS.items())


# --- Building the normalised view -------------------------------------------


def build_identity(facts: DeckFacts, overrides: dict[str, Any]) -> Identity:
    """Resolve company identity, with user-supplied values taking precedence."""

    def pick(key: str, fact_key: str | None = None) -> str:
        supplied = overrides.get(key)
        if isinstance(supplied, str) and supplied.strip():
            return supplied.strip()
        return facts.text("company", fact_key or key).strip()

    return Identity(
        name=pick("company_name", "name"),
        website=pick("website"),
        headquarters=pick("headquarters"),
        geography=pick("geography"),
        sector=pick("sector"),
        subsector=pick("subsector"),
        business_model=pick("business_model"),
        stage_text=pick("funding_stage", "stage"),
    )


def build_quality(
    facts: DeckFacts, classification: dict[str, Any], overrides: dict[str, Any]
) -> QualityFactors:
    """Resolve the quality factors and attach the scorecard scores."""

    def text(key: str) -> str:
        supplied = overrides.get(key)
        if isinstance(supplied, str) and supplied.strip():
            return supplied.strip()
        return facts.text("quality", key).strip()

    def count(key: str) -> float | None:
        supplied = overrides.get(key)
        if supplied is not None:
            return norm.parse_count(supplied)
        return norm.parse_count(facts.text("quality", key))

    quality = QualityFactors(
        ip_summary=text("ip_summary"),
        patents_filed=count("patents_filed"),
        patents_granted=count("patents_granted"),
        proprietary_technology=text("proprietary_technology"),
        proprietary_data=text("proprietary_data"),
        regulatory_approvals=text("regulatory_approvals"),
        regulatory_status=text("regulatory_status"),
        product_maturity=text("product_maturity"),
        differentiation=text("differentiation"),
        founder_experience=text("founder_experience"),
        management_quality=text("management_quality"),
        relevant_exits=text("relevant_exits"),
        domain_expertise=text("domain_expertise"),
        customer_validation=text("customer_validation"),
        strategic_partnerships=text("strategic_partnerships"),
    )

    for factor in SCORECARD_FACTORS:
        raw = classification.get(factor)
        if not isinstance(raw, dict):
            continue
        # A factor with no evidence on the slides is held at par. The model is
        # told this too, but enforcing it here means a stray non-zero score
        # cannot quietly inflate a scorecard valuation.
        absent = str(raw.get("evidence", "")).lower() != "present"
        score = 0 if absent else int(norm.clamp(float(raw.get("score") or 0), -2, 2))
        quality.scores[factor] = score
        quality.evidence[factor] = not absent
        rationale = str(raw.get("rationale") or "").strip()
        quality.score_rationales[factor] = (
            f"Not addressed in the deck. {rationale}".strip() if absent else rationale
        )

    return quality


def missing_quality_fields(quality: QualityFactors) -> list[str]:
    """The quality inputs section 1 asks for that nothing supplied."""
    labels = {
        "ip_summary": "Intellectual property position",
        "product_maturity": "Product maturity",
        "differentiation": "Competitive differentiation",
        "founder_experience": "Founder experience",
        "customer_validation": "Customer validation",
    }
    missing = [label for key, label in labels.items() if not getattr(quality, key)]
    if quality.patents_filed is None and quality.patents_granted is None and not quality.ip_summary:
        missing.append("Patents filed or granted")
    return missing


def stage_label(stage: str) -> str:
    return config.STAGE_LABELS.get(stage, stage)
