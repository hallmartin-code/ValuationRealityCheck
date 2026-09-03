"""Deck extraction and profile composition, without a model in the loop.

The extraction pass is split so that turning one model response into a
`CompanyProfile` is a pure function. These tests feed it a fixed response and
check the parts that decide whether the analysis is honest: source precedence,
unit normalisation, the revenue-basis classification, and consent.
"""

from __future__ import annotations

import json

import pytest

from app import config
from app.extraction import (
    company_extractor,
    engine,
    financial_extractor,
    financing_extractor,
    profile_builder,
)
from app.ingestion import deck_reader
from app.utils import normalization as norm

DECK = {"source": "example_deck.pdf", "slide_count": 14}


def response(facts: list[dict], scorecard: dict | None = None) -> dict:
    return {
        "facts": facts,
        "scorecard": scorecard
        or {
            factor: {"evidence": "absent", "score": 0, "rationale": ""}
            for factor in company_extractor.SCORECARD_FACTORS
        },
        "contradictions": [],
    }


def fact(field: str, text: str, slide: int = 1, quote: str = "") -> dict:
    return {"field": field, "text": text, "slide": slide, "quote": quote or text}


@pytest.fixture
def deck_facts():
    return engine.build_facts(
        response(
            [
                fact("company.name", "Northwind Analytics", 1),
                fact("company.sector", "B2B SaaS", 1),
                fact("company.stage", "Series A", 1),
                fact("financing.raise_amount", "$4M", 12),
                fact("financing.pre_money", "$18M", 12),
                fact("financing.instrument", "Priced equity", 12),
                fact("metrics.arr", "$2.4M", 8, "ARR reached $2.4M in Q4"),
                fact("metrics.growth_rate", "94%", 8),
                fact("metrics.gross_margin", "76%", 9),
                fact("metrics.nrr", "112%", 9),
                fact("metrics.customer_count", "38", 8),
                fact("metrics.burn", "$310k/month", 13),
            ]
        ),
        DECK,
    )


class TestSchemaAndPrompt:
    def test_the_field_enum_covers_all_three_extractors(self):
        names = set(engine.field_names())
        assert "company.name" in names
        assert "financing.valuation_cap" in names
        assert "metrics.nrr" in names
        assert "market.tam" in names
        assert "quality.patents_granted" in names

    def test_the_schema_is_valid_json_and_closed(self):
        schema = engine.build_schema()
        json.dumps(schema)
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"facts", "scorecard", "contradictions"}

    def test_the_prompt_carries_the_transcript_and_the_rules(self):
        prompt = engine.build_prompt({**DECK, "full_text": "--- Slide 1 ---\nNorthwind"})
        assert "--- Slide 1 ---" in prompt
        assert "do not compute ARR from MRR" in prompt
        assert "metrics.nrr:" in prompt

    def test_the_scorecard_schema_requires_an_evidence_flag(self):
        schema = company_extractor.scorecard_schema()
        for factor in company_extractor.SCORECARD_FACTORS:
            assert "evidence" in schema["properties"][factor]["required"]


class TestBuildFacts:
    def test_values_are_kept_verbatim_with_their_slide(self, deck_facts):
        entry = deck_facts.get("metrics", "arr")
        assert entry.text == "$2.4M"
        assert entry.slide == 8
        assert entry.citation() == "slide 8"

    def test_an_absent_field_is_absent_not_zero(self, deck_facts):
        assert not deck_facts.present("metrics", "ltv")
        assert deck_facts.text("metrics", "ltv") == ""

    def test_placeholder_text_reads_as_absent(self):
        facts = engine.build_facts(response([fact("metrics.nrr", "Not disclosed")]), DECK)
        assert not facts.present("metrics", "nrr")

    def test_an_unknown_field_name_is_dropped(self):
        facts = engine.build_facts(response([fact("metrics.invented_field", "$9M")]), DECK)
        assert not any(value.present for value in facts.metrics.values())

    def test_contradictions_are_carried_through(self):
        payload = response([])
        payload["contradictions"] = ["Slide 4 says $2.4M ARR; slide 11 says $3.1M."]
        assert engine.build_facts(payload, DECK).contradictions == payload["contradictions"]


class TestNormalisationIntoTheProfile:
    def test_deck_text_becomes_numbers_in_the_right_units(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, deck=DECK)
        assert profile.metrics.arr == pytest.approx(2_400_000)
        assert profile.metrics.growth_rate == pytest.approx(0.94)
        assert profile.metrics.nrr == pytest.approx(1.12)
        assert profile.round.raise_amount == pytest.approx(4_000_000)

    def test_monthly_burn_is_annualised_and_labelled_calculated(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, deck=DECK)
        assert profile.metrics.burn == pytest.approx(3_720_000)
        assert profile.ledger.get("burn").source_type == config.CALCULATED

    def test_post_money_is_derived_and_labelled_calculated(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, deck=DECK)
        assert profile.round.requested_post_money == pytest.approx(22_000_000)
        assert profile.ledger.get("requested_post_money").source_type == config.CALCULATED

    def test_acv_is_derived_from_revenue_and_customers(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, deck=DECK)
        assert profile.metrics.acv == pytest.approx(2_400_000 / 38)
        assert profile.ledger.get("acv").source_type == config.CALCULATED

    def test_mrr_only_becomes_arr_and_says_where_it_came_from(self):
        facts = engine.build_facts(response([fact("metrics.mrr", "$95k", 8)]), DECK)
        profile = profile_builder.build(facts, {}, deck=DECK)
        assert profile.metrics.arr == pytest.approx(1_140_000)
        assert profile.metrics.arr_basis == "MRR x 12"
        assert profile.ledger.get("arr").source_type == config.CALCULATED


class TestSourcePrecedence:
    def test_a_user_value_beats_the_deck(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, {"growth_rate": "71%"}, deck=DECK)
        assert profile.metrics.growth_rate == pytest.approx(0.71)
        assert profile.ledger.get("growth_rate").source_type == config.USER

    def test_the_deck_is_used_where_the_user_said_nothing(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, {"growth_rate": None}, deck=DECK)
        assert profile.metrics.growth_rate == pytest.approx(0.94)
        assert profile.ledger.get("growth_rate").source_type == config.DECK

    def test_a_user_company_name_overrides_the_deck(self, deck_facts):
        profile = profile_builder.build(
            deck_facts, {}, {"company_name": "Northwind Analytics Inc."}, deck=DECK
        )
        assert profile.identity.name == "Northwind Analytics Inc."

    def test_the_file_name_stands_in_when_the_deck_names_nobody(self):
        facts = engine.build_facts(response([]), DECK)
        profile = profile_builder.build(facts, {}, deck=DECK)
        assert profile.identity.name == "example deck"

    def test_unknown_input_keys_are_reported_not_swallowed(self):
        assert profile_builder.unknown_keys({"arr": 1, "revenu": 2}) == ["revenu"]


class TestRevenueBasis:
    def test_a_projected_label_excludes_the_figure_from_the_multiples(self):
        facts = engine.build_facts(
            response(
                [
                    fact("metrics.revenue", "$9M", 10),
                    fact("metrics.revenue_label", "2027 projected", 10),
                ]
            ),
            DECK,
        )
        profile = profile_builder.build(facts, {}, deck=DECK)
        assert profile.metrics.revenue == pytest.approx(9_000_000)
        assert profile.metrics.revenue_basis == norm.PROJECTED
        assert profile.metrics.countable_revenue is None

    def test_an_actual_label_keeps_the_figure_countable(self):
        facts = engine.build_facts(
            response(
                [
                    fact("metrics.revenue", "$3.1M", 10),
                    fact("metrics.revenue_label", "2025 actual", 10),
                ]
            ),
            DECK,
        )
        profile = profile_builder.build(facts, {}, deck=DECK)
        assert profile.metrics.countable_revenue == pytest.approx(3_100_000)

    def test_a_user_can_reclassify_the_basis(self):
        facts = engine.build_facts(
            response(
                [
                    fact("metrics.revenue", "$3.1M", 10),
                    fact("metrics.revenue_label", "2027 projected", 10),
                ]
            ),
            DECK,
        )
        profile = profile_builder.build(facts, {}, {"revenue_basis": "contracted"}, deck=DECK)
        assert profile.metrics.revenue_basis == norm.CONTRACTED
        assert profile.metrics.countable_revenue == pytest.approx(3_100_000)
        assert profile.ledger.get("revenue_basis").source_type == config.USER

    def test_the_reclassification_is_recorded_as_a_contradiction_note(self):
        facts = engine.build_facts(
            response(
                [
                    fact("metrics.revenue", "$9M", 10),
                    fact("metrics.revenue_label", "pipeline", 10),
                ]
            ),
            DECK,
        )
        profile = profile_builder.build(facts, {}, deck=DECK)
        assert any("pipeline" in text for text in profile.contradictions)


class TestScorecardClassification:
    def test_an_absent_factor_is_held_at_par(self):
        facts = engine.build_facts(response([]), DECK)
        scorecard = {
            factor: {"evidence": "absent", "score": 2, "rationale": "made up"}
            for factor in company_extractor.SCORECARD_FACTORS
        }
        profile = profile_builder.build(facts, scorecard, deck=DECK)
        # A stray non-zero score on an unevidenced factor must not inflate anything.
        assert set(profile.quality.scores.values()) == {0}
        assert not any(profile.quality.evidence.values())

    def test_an_evidenced_factor_keeps_its_score(self):
        facts = engine.build_facts(response([]), DECK)
        scorecard = {"team": {"evidence": "present", "score": 2, "rationale": "Two exits."}}
        profile = profile_builder.build(facts, scorecard, deck=DECK)
        assert profile.quality.score("team") == 2
        assert profile.quality.evidenced("team")

    def test_scores_are_clamped_to_the_scale(self):
        facts = engine.build_facts(response([]), DECK)
        scorecard = {"team": {"evidence": "present", "score": 9, "rationale": "x"}}
        profile = profile_builder.build(facts, scorecard, deck=DECK)
        assert profile.quality.score("team") == 2


class TestConsent:
    def test_consent_is_false_unless_explicitly_true(self):
        facts = engine.build_facts(response([]), DECK)
        for supplied in ({}, {"analysis_consent": "yes"}, {"analysis_consent": 1}):
            profile = profile_builder.build(facts, {}, supplied, deck=DECK)
            assert profile.user.consent.analysis_consent is False
            assert profile.user.consent.timestamp is None

    def test_a_literal_true_is_honoured_and_stamped(self):
        facts = engine.build_facts(response([]), DECK)
        profile = profile_builder.build(facts, {}, {"analysis_consent": True}, deck=DECK)
        assert profile.user.consent.analysis_consent is True
        assert profile.user.consent.timestamp


class TestComparablesFile:
    def test_a_csv_is_read_and_comment_rows_ignored(self, tmp_path):
        path = tmp_path / "comps.csv"
        path.write_text(
            "company,stage,pre_money,arr,source\n"
            "# a note the analyst left in the file\n"
            "Example Co,Series A,$24M,$2.6M,PitchBook\n"
            "Blank Row,Series A,,,\n",
            encoding="utf-8",
        )
        comparables = financing_extractor.load_comparables(path)
        assert [item.company for item in comparables] == ["Example Co", "Blank Row"]
        assert [item.usable for item in comparables] == [True, False]
        assert comparables[0].pre_money == pytest.approx(24_000_000)

    def test_a_json_file_is_read_from_either_shape(self, tmp_path):
        rows = [{"company": "Example Co", "pre_money": 24_000_000, "arr": 2_600_000}]
        flat = tmp_path / "flat.json"
        flat.write_text(json.dumps(rows), encoding="utf-8")
        wrapped = tmp_path / "wrapped.json"
        wrapped.write_text(json.dumps({"comparables": rows}), encoding="utf-8")
        assert len(financing_extractor.load_comparables(flat)) == 1
        assert len(financing_extractor.load_comparables(wrapped)) == 1

    def test_prose_column_headings_are_accepted(self):
        """A hand-built comp sheet writes its headings as prose, not field names."""
        comparable = financing_extractor.parse_comparable(
            {
                "Comparable Company": "Example Co",
                "Pre-money valuation": "$24M",
                "ARR at financing": "$2.6M",
                "Growth at financing": "80%",
                "Lead investor": "Example Ventures",
            }
        )
        assert comparable.company == "Example Co"
        assert comparable.pre_money == pytest.approx(24_000_000)
        assert comparable.arr == pytest.approx(2_600_000)
        assert comparable.growth == pytest.approx(0.80)
        assert comparable.lead_investor == "Example Ventures"

    def test_an_unrecognised_column_is_ignored_not_fatal(self):
        comparable = financing_extractor.parse_comparable(
            {"company": "Example Co", "pre_money": "$24M", "analyst note": "looks rich"}
        )
        assert comparable.company == "Example Co"
        assert comparable.usable

    def test_no_file_means_no_comparables_not_an_error(self):
        assert financing_extractor.load_comparables(None) == []

    def test_a_missing_file_is_an_explicit_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            financing_extractor.load_comparables(tmp_path / "absent.csv")

    def test_the_shipped_template_parses(self):
        path = config.PROJECT_ROOT / "templates" / "comparables.csv"
        comparables = financing_extractor.load_comparables(path)
        assert len(comparables) == 3
        assert all(item.usable for item in comparables)

    def test_an_implied_multiple_is_pre_money_over_revenue(self):
        comparable = financing_extractor.parse_comparable(
            {"company": "Example Co", "pre_money": "$24M", "arr": "$2.6M"}
        )
        assert comparable.implied_multiple == pytest.approx(24 / 2.6, rel=1e-6)

    def test_a_comparable_completes_its_own_round_arithmetic(self):
        comparable = financing_extractor.parse_comparable(
            {"company": "Example Co", "post_money": "$30M", "round_size": "$6M"}
        )
        assert comparable.pre_money == pytest.approx(24_000_000)


class TestMissingInformation:
    def test_gaps_are_listed_rather_than_filled(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, deck=DECK)
        assert "Comparable financing data" in profile.missing_information
        assert "Runway" in profile.missing_information
        assert "Intellectual property position" in profile.missing_information

    def test_a_supplied_metric_leaves_the_list(self, deck_facts):
        profile = profile_builder.build(deck_facts, {}, {"runway_months": 14}, deck=DECK)
        assert "Runway" not in profile.missing_information


class TestDeckDispatch:
    @pytest.mark.parametrize(
        "name,kind", [("a.pdf", "pdf"), ("a.pptx", "pptx"), ("a.PPT", "pptx")]
    )
    def test_supported_extensions(self, name, kind):
        assert deck_reader.detect_kind(name) == kind

    def test_an_unsupported_extension_is_rejected_by_name(self):
        with pytest.raises(ValueError, match=r"\.docx"):
            deck_reader.detect_kind("deck.docx")

    def test_a_missing_deck_is_an_explicit_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            deck_reader.read_deck(tmp_path / "absent.pdf")

    def test_legacy_ppt_explains_the_fix_rather_than_crashing(self, tmp_path):
        path = tmp_path / "old.ppt"
        path.write_bytes(b"\xd0\xcf\x11\xe0")
        with pytest.raises(ValueError, match="save as .pptx"):
            deck_reader.read_deck(path)

    def test_the_transcript_labels_slides(self):
        slides = [
            {"index": 1, "title": "Cover", "text": "Northwind", "tables": []},
            {"index": 2, "title": "ARR", "text": "$2.4M", "tables": [[["Year", "ARR"]]]},
        ]
        transcript = deck_reader.render_transcript(slides)
        assert "--- Slide 1: Cover ---" in transcript
        assert "Year | ARR" in transcript
