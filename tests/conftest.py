"""Shared fixtures: the ten company scenarios section 20 asks the suite to cover.

Every scenario is built as a `CompanyProfile` directly rather than through the
extraction pass, so the whole suite runs offline with no API key and no deck
file. That is the point of keeping extraction and valuation in separate
packages: the arithmetic can be verified without the model in the loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.company import (  # noqa: E402
    Comparable,
    CompanyProfile,
    Identity,
    OperatingMetrics,
    QualityFactors,
    RoundTerms,
)


def scored(profile: CompanyProfile, **scores: int) -> CompanyProfile:
    """Attach scorecard scores as an evidenced classification would."""
    profile.quality.scores = dict(scores)
    profile.quality.evidence = {factor: True for factor in scores}
    profile.quality.score_rationales = {
        factor: f"Scored {value} from the deck." for factor, value in scores.items()
    }
    return profile


# --- 1. Pre-revenue seed company --------------------------------------------


@pytest.fixture
def pre_revenue() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Kestrel Robotics",
            sector="Industrial robotics",
            geography="United States",
            stage_text="Pre-seed",
        ),
        round=RoundTerms(
            raise_amount=1_500_000,
            instrument="Priced equity",
            requested_pre_money=6_000_000,
        ),
        metrics=OperatingMetrics(som=250_000_000),
        quality=QualityFactors(
            product_maturity="Working prototype in the lab",
            founder_experience="Two robotics PhDs, first company",
            patents_filed=2,
        ),
    )
    return scored(
        profile,
        team=1,
        opportunity=1,
        product=-1,
        defensibility=1,
        traction=-1,
        partnerships=0,
    )


# --- 2. SaaS company with ARR and high growth -------------------------------


@pytest.fixture
def saas() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Northwind Analytics",
            sector="B2B SaaS",
            geography="United States",
            stage_text="Series A",
        ),
        round=RoundTerms(
            raise_amount=4_000_000,
            instrument="Priced equity",
            requested_pre_money=18_000_000,
            committed=1_500_000,
        ),
        metrics=OperatingMetrics(
            arr=2_400_000,
            growth_rate=0.94,
            gross_margin=0.76,
            nrr=1.12,
            customer_count=38,
            largest_customer_pct=0.08,
            burn=3_000_000,
            runway_months=14,
            ltv_cac=4.2,
        ),
        quality=QualityFactors(
            founder_experience="Two prior exits in data infrastructure",
            differentiation="Only product with native warehouse execution",
            customer_validation="38 paying customers, 4 named references",
        ),
    )
    return scored(
        profile,
        team=2,
        opportunity=1,
        product=1,
        defensibility=0,
        traction=2,
        partnerships=0,
    )


# --- 3. Hardware company with lower gross margin ----------------------------


@pytest.fixture
def hardware() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Ironvale Systems",
            sector="Industrial hardware",
            geography="Germany",
            stage_text="Series A",
        ),
        round=RoundTerms(
            raise_amount=6_000_000,
            instrument="Priced equity",
            requested_pre_money=22_000_000,
        ),
        metrics=OperatingMetrics(
            revenue=3_100_000,
            growth_rate=0.42,
            gross_margin=0.34,
            customer_count=21,
            largest_customer_pct=0.18,
            burn=4_200_000,
            runway_months=11,
            sales_cycle_months=11,
        ),
        quality=QualityFactors(
            patents_granted=4,
            product_maturity="In production, third generation",
            proprietary_technology="Proprietary thermal control stack",
        ),
    )
    return scored(
        profile,
        team=0,
        opportunity=1,
        product=1,
        defensibility=2,
        traction=1,
        partnerships=1,
    )


# --- 4. Life science company awaiting regulatory approval -------------------


@pytest.fixture
def life_science() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Halcyon Bio",
            sector="Therapeutics",
            geography="United States",
            stage_text="Seed",
        ),
        round=RoundTerms(
            raise_amount=2_000_000,
            instrument="SAFE",
            valuation_cap=12_000_000,
            discount_rate=0.20,
        ),
        metrics=OperatingMetrics(som=400_000_000),
        quality=QualityFactors(
            patents_filed=3,
            regulatory_status="Pre-IND, filing targeted next year",
            product_maturity="Preclinical, efficacy shown in two models",
            domain_expertise="Founding scientist is the field's leading author",
        ),
    )
    return scored(
        profile,
        team=2,
        opportunity=2,
        product=-1,
        defensibility=1,
        traction=0,
        partnerships=0,
    )


# --- 5. Company with high customer concentration ----------------------------


@pytest.fixture
def concentrated() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Meridian Data",
            sector="B2B SaaS",
            geography="United States",
            stage_text="Series A",
        ),
        round=RoundTerms(
            raise_amount=5_000_000,
            instrument="Priced equity",
            requested_pre_money=20_000_000,
        ),
        metrics=OperatingMetrics(
            arr=2_800_000,
            growth_rate=0.55,
            gross_margin=0.71,
            nrr=1.02,
            customer_count=6,
            largest_customer_pct=0.62,
            runway_months=16,
        ),
        quality=QualityFactors(customer_validation="Six enterprise accounts"),
    )
    return scored(
        profile,
        team=0,
        opportunity=1,
        product=1,
        defensibility=-1,
        traction=0,
        partnerships=0,
    )


# --- 6. Requested valuation materially above the estimated range ------------


@pytest.fixture
def overpriced() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Zenith Labs",
            sector="B2B SaaS",
            geography="United States",
            stage_text="Seed",
        ),
        round=RoundTerms(
            raise_amount=3_000_000,
            instrument="Priced equity",
            requested_pre_money=60_000_000,
        ),
        metrics=OperatingMetrics(
            arr=900_000,
            growth_rate=0.40,
            gross_margin=0.68,
            customer_count=14,
            largest_customer_pct=0.20,
            runway_months=13,
        ),
        quality=QualityFactors(product_maturity="In market"),
    )
    return scored(profile, team=0, opportunity=0, product=0, defensibility=0, traction=0)


# --- 7. No comparable financing data ----------------------------------------


@pytest.fixture
def no_comparables(saas: CompanyProfile) -> CompanyProfile:
    saas.comparables = []
    return saas


# --- 8. SAFE financing ------------------------------------------------------


@pytest.fixture
def safe_round() -> CompanyProfile:
    profile = CompanyProfile(
        identity=Identity(
            name="Pellucid",
            sector="Developer tools",
            geography="Canada",
            stage_text="Seed",
        ),
        round=RoundTerms(
            raise_amount=2_500_000,
            instrument="SAFE (post-money)",
            valuation_cap=15_000_000,
            discount_rate=0.20,
            prior_raised=750_000,
        ),
        metrics=OperatingMetrics(
            mrr=95_000,
            growth_rate=0.85,
            gross_margin=0.82,
            nrr=1.08,
            customer_count=260,
        ),
        quality=QualityFactors(product_maturity="In market", proprietary_data="Usage corpus"),
    )
    return scored(profile, team=1, opportunity=1, product=1, defensibility=0, traction=1)


# --- 9. Priced equity financing with a full comp set ------------------------


@pytest.fixture
def comparables_set() -> list[Comparable]:
    return [
        Comparable(
            company="Example Analytics Co",
            sector="B2B SaaS",
            stage="Series A",
            financing_date="2025-06",
            round_size=6_000_000,
            pre_money=24_000_000,
            arr=2_600_000,
            growth=0.80,
            geography="United States",
            source="PitchBook",
        ),
        Comparable(
            company="Second Example Inc",
            sector="B2B SaaS",
            stage="Series A",
            financing_date="2025-03",
            round_size=8_000_000,
            pre_money=32_000_000,
            arr=4_100_000,
            growth=0.65,
            geography="United States",
            source="Company press release",
        ),
        Comparable(
            company="Third Example Ltd",
            sector="B2B SaaS",
            stage="Series A",
            financing_date="2024-11",
            round_size=3_000_000,
            pre_money=12_000_000,
            arr=1_200_000,
            growth=1.10,
            geography="United States",
            source="Crunchbase",
        ),
        Comparable(
            company="Fourth Example GmbH",
            sector="B2B SaaS",
            stage="Series A",
            financing_date="2025-01",
            round_size=5_000_000,
            pre_money=19_000_000,
            arr=2_100_000,
            growth=0.70,
            geography="United States",
            source="PitchBook",
        ),
    ]


@pytest.fixture
def priced_with_comparables(saas: CompanyProfile, comparables_set) -> CompanyProfile:
    saas.comparables = list(comparables_set)
    return saas


# --- 10. Deck with incomplete financial data --------------------------------


@pytest.fixture
def incomplete() -> CompanyProfile:
    """Almost nothing disclosed: a name, a sector and an ask."""
    return CompanyProfile(
        identity=Identity(name="Opaque Ltd", sector="Marketplace", stage_text="Seed"),
        round=RoundTerms(raise_amount=1_000_000, instrument="Convertible note"),
        metrics=OperatingMetrics(),
        quality=QualityFactors(),
    )
