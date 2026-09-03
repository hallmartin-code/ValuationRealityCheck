"""Assembling the CompanyProfile the valuation engine reads.

This is where section 2's precedence order is applied end to end: user input,
then the deck, then a calculated value, then an assumption. The three extractor
modules do the reading; this module composes their answers into one object,
records what is missing, and carries the contradictions forward rather than
resolving them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..models.company import (
    Comparable,
    CompanyProfile,
    Consent,
    DeckFacts,
    Ledger,
    UserContext,
)
from . import company_extractor, financial_extractor, financing_extractor

# Keys a user-input file may carry. Anything else is reported rather than
# silently ignored, because a typo'd key would otherwise look like a metric the
# user never supplied.
USER_KEYS: frozenset[str] = frozenset(
    {
        "user_name",
        "user_email",
        "company_name",
        "website",
        "headquarters",
        "geography",
        "sector",
        "subsector",
        "business_model",
        "funding_stage",
        "revenue_basis",
        "analysis_consent",
        "data_storage_consent",
        "contact_consent",
        "comparables",
    }
    | set(financing_extractor.FIELDS["financing"])
    | {"requested_pre_money", "requested_post_money"}
    | set(financial_extractor.FIELDS["metrics"])
    | set(financial_extractor.FIELDS["market"])
    | {"runway_months", "sales_cycle_months"}
    | set(company_extractor.FIELDS["quality"])
)


def build(
    facts: DeckFacts,
    scorecard: dict[str, Any],
    overrides: dict[str, Any] | None = None,
    comparables: list[Comparable] | None = None,
    deck: dict[str, Any] | None = None,
    analysis_date: date | None = None,
) -> CompanyProfile:
    """Compose the normalised company profile from every available source."""
    overrides = dict(overrides or {})
    deck = deck or {}
    ledger = Ledger()
    as_of = analysis_date or date.today()

    identity = company_extractor.build_identity(facts, overrides)
    terms, contradictions = financing_extractor.build_round(facts, overrides, ledger)
    metrics = financial_extractor.build_metrics(facts, overrides, ledger, as_of.year)
    quality = company_extractor.build_quality(facts, scorecard, overrides)

    supplied = list(comparables or [])
    for raw in overrides.get("comparables") or []:
        parsed = financing_extractor.parse_comparable(raw)
        if parsed is not None:
            supplied.append(parsed)
    financing_extractor.record_comparables(supplied, ledger)

    profile = CompanyProfile(
        identity=identity,
        round=terms,
        metrics=metrics,
        quality=quality,
        comparables=supplied,
        user=_user(overrides),
        ledger=ledger,
        deck_source=deck.get("source", facts.source_file),
        slide_count=deck.get("slide_count", facts.slide_count),
        analysis_date=as_of,
    )

    if not profile.identity.name:
        profile.identity.name = _fallback_name(deck, facts)

    for text in list(facts.contradictions) + contradictions:
        profile.note_contradiction(text)

    _note_gaps(profile)
    return profile


def _user(overrides: dict[str, Any]) -> UserContext:
    """Build the user context. Consent is read only from explicit true values.

    Section 16: consent is never inferred. A key that is absent, empty, or any
    value other than a literal boolean true leaves the flag False.
    """
    consent = Consent(
        analysis_consent=overrides.get("analysis_consent") is True,
        data_storage_consent=overrides.get("data_storage_consent") is True,
        contact_consent=overrides.get("contact_consent") is True,
    )
    consent.stamp()
    return UserContext(
        name=str(overrides.get("user_name") or "").strip(),
        email=str(overrides.get("user_email") or "").strip(),
        consent=consent,
    )


def _fallback_name(deck: dict[str, Any], facts: DeckFacts) -> str:
    """Name the analysis after the file when the deck never names the company."""
    from pathlib import Path

    source = deck.get("source") or facts.source_file
    return Path(source).stem.replace("_", " ").strip() if source else "Company"


def _note_gaps(profile: CompanyProfile) -> None:
    """Fill the missing-information list from all three extractors."""
    for label in financing_extractor.missing_round_fields(profile.round):
        profile.note_missing(label)
    for label in financial_extractor.missing_metrics(profile.metrics):
        profile.note_missing(label)
    for label in company_extractor.missing_quality_fields(profile.quality):
        profile.note_missing(label)
    if not profile.usable_comparables:
        profile.note_missing("Comparable financing data")
    note = financial_extractor.revenue_note(profile.metrics)
    if note:
        profile.note_contradiction(note)


def unknown_keys(overrides: dict[str, Any]) -> list[str]:
    """Keys in a user-input file that this application does not recognise."""
    return sorted(key for key in overrides if key not in USER_KEYS)
