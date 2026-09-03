"""The structured TEN Capital record (section 16), plus the audit trail behind it.

The schema section 16 specifies is emitted exactly, key for key, in the order it
was specified. Four additional top-level keys carry the material a reader needs
to reproduce the number — the bridge, the sensitivity grid, the validation
findings and the source ledger. They are additions, never substitutions: a
consumer written against the specified schema reads this file unchanged.

Consent is copied from the profile and never derived. `verify_schema` exists so
a test can assert that, rather than trusting that it stayed true.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import config
from ..models.company import CompanyProfile, to_plain
from ..models.valuation import ValuationAnalysis
from ..utils import formatting as fmt
from ..valuation import dilution as dilution_model

# The specified schema, as a template. Order here is the order in the file.
SPEC_KEYS = (
    "analysis_id",
    "analysis_timestamp",
    "user",
    "company",
    "metrics",
    "round",
    "valuation",
    "dilution",
    "methods",
    "premium_factors",
    "discount_factors",
    "assumptions",
    "missing_information",
    "sources",
    "consent",
)

# Keys this application adds beyond the specified schema, to make the analysis
# reproducible rather than merely reportable.
EXTRA_KEYS = ("valuation_bridge", "sensitivity", "validation", "source_ledger", "deck")


def build(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    analysis_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Build the complete record."""
    record: dict[str, Any] = {
        "analysis_id": analysis_id or str(uuid.uuid4()),
        "analysis_timestamp": timestamp
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "user": {"name": profile.user.name, "email": profile.user.email},
        "company": {
            "name": profile.identity.name,
            "website": profile.identity.website,
            "sector": profile.identity.sector,
            "subsector": profile.identity.subsector,
            "stage": profile.identity.stage_text or config.STAGE_LABELS[profile.stage],
            "geography": profile.identity.geography,
        },
        "metrics": _metrics(profile),
        "round": _round(profile),
        "valuation": _valuation(analysis),
        "dilution": _dilution(analysis),
        "methods": _methods(analysis),
        "premium_factors": _factors(analysis.premiums(), analysis.adjustment_applied_share),
        "discount_factors": _factors(analysis.discounts(), analysis.adjustment_applied_share),
        "assumptions": list(analysis.assumptions),
        "missing_information": list(profile.missing_information),
        "sources": _sources(profile),
        "consent": {
            "analysis_consent": profile.user.consent.analysis_consent,
            "data_storage_consent": profile.user.consent.data_storage_consent,
            "contact_consent": profile.user.consent.contact_consent,
            "timestamp": profile.user.consent.timestamp,
        },
    }

    record["valuation_bridge"] = _bridge(analysis)
    record["sensitivity"] = _sensitivity(analysis)
    record["validation"] = _validation(profile, analysis)
    record["source_ledger"] = _ledger(profile)
    record["deck"] = {
        "source_file": profile.deck_source,
        "slide_count": profile.slide_count,
        "analysis_date": profile.analysis_date.isoformat(),
    }
    return record


def write(record: dict[str, Any], path: str | Path) -> Path:
    """Write the record as indented JSON and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


# --- Sections ---------------------------------------------------------------


def _metrics(profile: CompanyProfile) -> dict[str, Any]:
    metrics = profile.metrics
    return {
        "revenue": metrics.revenue,
        "arr": metrics.arr,
        "growth_rate": metrics.growth_rate,
        "gross_margin": metrics.gross_margin,
        "retention": metrics.retention,
        "nrr": metrics.nrr,
        "customer_count": metrics.customer_count,
        "largest_customer_pct": metrics.largest_customer_pct,
        "burn": metrics.burn,
        "runway_months": metrics.runway_months,
        # Beyond the specified keys, but part of what the valuation used.
        "mrr": metrics.mrr,
        "revenue_basis": metrics.revenue_basis,
        "countable_revenue": metrics.countable_revenue,
        "acv": metrics.acv,
        "gross_retention": metrics.gross_retention,
        "churn": metrics.churn,
        "cac": metrics.cac,
        "ltv": metrics.ltv,
        "ltv_cac": metrics.ltv_cac,
        "sales_cycle_months": metrics.sales_cycle_months,
        "pipeline": metrics.pipeline,
        "customer_growth": metrics.customer_growth,
        "ebitda": metrics.ebitda,
        "tam": metrics.tam,
        "sam": metrics.sam,
        "som": metrics.som,
    }


def _round(profile: CompanyProfile) -> dict[str, Any]:
    terms = profile.round
    return {
        "raise_amount": terms.raise_amount,
        "instrument": terms.instrument,
        "requested_pre_money": terms.requested_pre_money,
        "requested_post_money": terms.requested_post_money,
        "valuation_cap": terms.valuation_cap,
        "discount_rate": terms.discount_rate,
        "committed": terms.committed,
        "prior_raised": terms.prior_raised,
        "is_convertible": terms.is_convertible,
        "reference_valuation": terms.reference_valuation,
        "reference_basis": terms.reference_basis,
    }


def _valuation(analysis: ValuationAnalysis) -> dict[str, Any]:
    return {
        "low": analysis.range.low,
        "base": analysis.range.base,
        "high": analysis.range.high,
        "requested_gap_amount": analysis.requested_gap_amount,
        "requested_gap_percent": analysis.requested_gap_percent,
        "requested_status": analysis.requested_status,
        "confidence": analysis.confidence,
        "confidence_reason": analysis.confidence_reason,
        "requested_valuation": analysis.requested_valuation,
        "requested_statement": analysis.requested_statement,
        "stage_band": analysis.stage,
        "weighted_baseline": analysis.baseline.base if analysis.baseline else None,
        "net_adjustment": analysis.net_adjustment,
        "adjustment_applied_share": analysis.adjustment_applied_share,
        "decision_factors": list(analysis.decision_factors),
        "key_milestone": analysis.key_milestone,
        "comparable_note": analysis.comparable_note,
    }


def _dilution(analysis: ValuationAnalysis) -> dict[str, Any]:
    """The four cases, keyed the way section 16 names them."""
    keys = {
        dilution_model.LOW: "low_case",
        dilution_model.BASE: "base_case",
        dilution_model.HIGH: "high_case",
        dilution_model.REQUESTED: "requested_case",
    }
    record: dict[str, Any] = {name: None for name in keys.values()}
    detail: list[dict[str, Any]] = []
    for case in analysis.dilution:
        if case.label in keys:
            record[keys[case.label]] = case.new_investor_pct
        detail.append(
            {
                "case": case.label,
                "pre_money": case.pre_money,
                "post_money": case.post_money,
                "new_investor_pct": case.new_investor_pct,
                "note": case.note,
            }
        )
    record["cases"] = detail
    return record


def _methods(analysis: ValuationAnalysis) -> list[dict[str, Any]]:
    """Every method considered, including the ones that excluded themselves."""
    return [
        {
            "method": method.name,
            "label": fmt.method_label(method.name),
            "included": method.included,
            "low": method.low,
            "base": method.base,
            "high": method.high,
            "weight": method.weight,
            "basis": method.basis,
            "detail": method.detail,
            "quality_adjusted": method.quality_adjusted,
            "exclusion_reason": method.exclusion_reason,
            "inputs": to_plain(method.inputs),
        }
        for method in analysis.methods
    ]


def _factors(items: list[Any], share: float) -> list[dict[str, Any]]:
    return [
        {
            "label": item.label,
            "metric": item.metric,
            "nominal_pct": item.pct,
            "signed_pct": item.signed_pct,
            "effective_pct": item.signed_pct * share,
            "rationale": item.rationale,
        }
        for item in items
    ]


def _bridge(analysis: ValuationAnalysis) -> list[dict[str, Any]]:
    return [
        {"label": line.label, "pct": line.pct, "value": line.value, "is_total": line.is_total}
        for line in analysis.bridge
    ]


def _sensitivity(analysis: ValuationAnalysis) -> dict[str, Any]:
    grid = analysis.sensitivity
    return {
        "title": grid.title,
        "row_axis": grid.row_axis,
        "column_axis": grid.column_axis,
        "column_labels": list(grid.column_labels),
        "rows": [{"label": label, "cells": list(cells)} for label, cells in grid.rows],
        "base_cell": list(grid.base_cell) if grid.base_cell else None,
        "note": grid.note,
    }


def _validation(profile: CompanyProfile, analysis: ValuationAnalysis) -> dict[str, Any]:
    return {
        "issues": [
            {"severity": issue.severity, "rule": issue.rule, "message": issue.message}
            for issue in analysis.issues
        ],
        "error_count": len(analysis.errors()),
        "contradictions": list(profile.contradictions),
    }


def _ledger(profile: CompanyProfile) -> list[dict[str, Any]]:
    """The full provenance record: metric, value, source type, citation, confidence."""
    return [
        {
            "metric": entry.metric,
            "value": entry.value,
            "source_type": entry.source_type,
            "source_label": config.SOURCE_LABELS[entry.source_type],
            "source": entry.source,
            "confidence": entry.confidence,
            "unit": entry.unit,
            "notes": entry.notes,
        }
        for entry in profile.ledger.entries
    ]


def _sources(profile: CompanyProfile) -> list[dict[str, Any]]:
    """Where the material inputs came from: the deck, the user, and the comparables."""
    sources: list[dict[str, Any]] = []
    if profile.deck_source:
        sources.append(
            {
                "kind": "pitch_deck",
                "reference": profile.deck_source,
                "detail": f"{profile.slide_count} slides",
            }
        )
    if any(entry.source_type == config.USER for entry in profile.ledger.entries):
        supplied = [
            entry.metric
            for entry in profile.ledger.entries
            if entry.source_type == config.USER
        ]
        sources.append(
            {
                "kind": "user_input",
                "reference": profile.user.email or profile.user.name or "user",
                "detail": f"{len(supplied)} values supplied: {', '.join(sorted(supplied))}",
            }
        )
    for comparable in profile.comparables:
        sources.append(
            {
                "kind": "comparable_financing",
                "reference": comparable.company,
                "detail": comparable.label(),
                "source": comparable.source,
                "source_date": comparable.source_date,
                "pre_money": comparable.pre_money,
                "usable": comparable.usable,
            }
        )
    if not profile.comparables:
        sources.append({"kind": "comparable_financing", "reference": config.NO_COMPARABLES})
    return sources


# --- Schema verification ----------------------------------------------------

# The exact shape section 16 specifies, as {top-level key: required child keys}.
REQUIRED: dict[str, tuple[str, ...]] = {
    "analysis_id": (),
    "analysis_timestamp": (),
    "user": ("name", "email"),
    "company": ("name", "website", "sector", "subsector", "stage", "geography"),
    "metrics": (
        "revenue",
        "arr",
        "growth_rate",
        "gross_margin",
        "retention",
        "nrr",
        "customer_count",
        "largest_customer_pct",
        "burn",
        "runway_months",
    ),
    "round": (
        "raise_amount",
        "instrument",
        "requested_pre_money",
        "requested_post_money",
        "valuation_cap",
    ),
    "valuation": (
        "low",
        "base",
        "high",
        "requested_gap_amount",
        "requested_gap_percent",
        "requested_status",
        "confidence",
    ),
    "dilution": ("low_case", "base_case", "high_case", "requested_case"),
    "methods": (),
    "premium_factors": (),
    "discount_factors": (),
    "assumptions": (),
    "missing_information": (),
    "sources": (),
    "consent": (
        "analysis_consent",
        "data_storage_consent",
        "contact_consent",
        "timestamp",
    ),
}


def verify_schema(record: dict[str, Any]) -> list[str]:
    """Return the ways `record` departs from section 16's schema. Empty means it conforms."""
    problems: list[str] = []
    for key, children in REQUIRED.items():
        if key not in record:
            problems.append(f"missing top-level key: {key}")
            continue
        if not children:
            continue
        value = record[key]
        if not isinstance(value, dict):
            problems.append(f"{key} must be an object")
            continue
        for child in children:
            if child not in value:
                problems.append(f"missing key: {key}.{child}")

    for key in ("methods", "premium_factors", "discount_factors", "assumptions",
                "missing_information", "sources"):
        if key in record and not isinstance(record[key], list):
            problems.append(f"{key} must be a list")

    consent = record.get("consent")
    if isinstance(consent, dict):
        for flag in ("analysis_consent", "data_storage_consent", "contact_consent"):
            if not isinstance(consent.get(flag), bool):
                problems.append(f"consent.{flag} must be a boolean")
    return problems
