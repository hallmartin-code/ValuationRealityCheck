"""The structured TEN Capital record, and the rules it has to keep.

Two things are checked: that the file conforms to the schema section 16
specifies, and that the record is a faithful copy of what the page printed — a
record that disagrees with the PDF is worse than no record.
"""

from __future__ import annotations

import json

import pytest

from app import config
from app.models.company import Consent, CompanyProfile
from app.output import json_export
from app.utils import validation
from app.valuation import valuation_engine

SCENARIOS = (
    "pre_revenue",
    "saas",
    "hardware",
    "life_science",
    "concentrated",
    "overpriced",
    "no_comparables",
    "safe_round",
    "priced_with_comparables",
    "incomplete",
)


def record_for(profile: CompanyProfile) -> dict:
    analysis = valuation_engine.run(profile)
    analysis.issues = validation.run(profile, analysis)
    return json_export.build(profile, analysis)


@pytest.fixture
def record(saas) -> dict:
    return record_for(saas)


class TestSchema:
    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_every_scenario_conforms_to_the_specified_schema(self, scenario, request):
        problems = json_export.verify_schema(record_for(request.getfixturevalue(scenario)))
        assert problems == []

    def test_the_specified_keys_come_first_and_in_order(self, record):
        keys = list(record)
        assert keys[: len(json_export.SPEC_KEYS)] == list(json_export.SPEC_KEYS)

    def test_the_additions_are_additions_not_substitutions(self, record):
        assert set(json_export.EXTRA_KEYS).issubset(record)
        assert set(json_export.SPEC_KEYS).issubset(record)

    def test_the_record_is_json_serialisable(self, record):
        reloaded = json.loads(json.dumps(record, default=str))
        assert reloaded["company"]["name"] == "Northwind Analytics"

    def test_it_writes_to_disk_and_reads_back(self, record, tmp_path):
        path = json_export.write(record, tmp_path / "nested" / "record.json")
        assert json.loads(path.read_text(encoding="utf-8"))["analysis_id"] == record["analysis_id"]


class TestSchemaVerifierItself:
    def test_a_missing_top_level_key_is_caught(self, record):
        del record["valuation"]
        assert "missing top-level key: valuation" in json_export.verify_schema(record)

    def test_a_missing_child_key_is_caught(self, record):
        del record["metrics"]["nrr"]
        assert "missing key: metrics.nrr" in json_export.verify_schema(record)

    def test_a_non_boolean_consent_flag_is_caught(self, record):
        record["consent"]["analysis_consent"] = "yes"
        assert any(
            "consent.analysis_consent" in problem
            for problem in json_export.verify_schema(record)
        )


class TestConsent:
    def test_consent_defaults_to_false_and_carries_no_timestamp(self, record):
        consent = record["consent"]
        assert consent == {
            "analysis_consent": False,
            "data_storage_consent": False,
            "contact_consent": False,
            "timestamp": None,
        }

    def test_consent_is_never_inferred_from_a_completed_analysis(self, priced_with_comparables):
        """A full, high-confidence run still records no consent."""
        consent = record_for(priced_with_comparables)["consent"]
        assert not any(value for key, value in consent.items() if key != "timestamp")

    def test_explicit_consent_is_recorded_and_stamped(self, saas):
        saas.user.consent = Consent(analysis_consent=True)
        saas.user.consent.stamp()
        consent = record_for(saas)["consent"]
        assert consent["analysis_consent"] is True
        assert consent["data_storage_consent"] is False
        assert consent["timestamp"] is not None

    def test_no_consent_leaves_the_stamp_empty(self):
        consent = Consent()
        consent.stamp()
        assert consent.timestamp is None

    def test_a_stamp_without_consent_is_a_validation_error(self, saas):
        saas.user.consent.timestamp = "2026-09-03T10:00:00+00:00"
        analysis = valuation_engine.run(saas)
        issues = validation.run(saas, analysis)
        assert any(issue.rule == "consent.inferred" for issue in issues)


class TestFidelityToThePage:
    """The record must repeat the analysis, not restate it."""

    def test_the_valuation_matches_the_analysis(self, saas):
        analysis = valuation_engine.run(saas)
        record = json_export.build(saas, analysis)
        assert record["valuation"]["low"] == pytest.approx(analysis.range.low)
        assert record["valuation"]["base"] == pytest.approx(analysis.range.base)
        assert record["valuation"]["high"] == pytest.approx(analysis.range.high)
        assert record["valuation"]["confidence"] == analysis.confidence

    def test_the_gap_matches_the_analysis(self, overpriced):
        analysis = valuation_engine.run(overpriced)
        record = json_export.build(overpriced, analysis)
        assert record["valuation"]["requested_status"] == config.MATERIAL_GAP
        assert record["valuation"]["requested_gap_amount"] == pytest.approx(
            analysis.requested_gap_amount
        )

    def test_dilution_is_keyed_the_way_the_schema_names_it(self, saas):
        analysis = valuation_engine.run(saas)
        record = json_export.build(saas, analysis)
        for key, label in (
            ("low_case", "Low"),
            ("base_case", "Base"),
            ("high_case", "High"),
            ("requested_case", "Requested"),
        ):
            assert record["dilution"][key] == pytest.approx(
                analysis.dilution_case(label).new_investor_pct
            )

    def test_the_bridge_in_the_record_reconciles(self, saas):
        record = record_for(saas)
        assert record["valuation_bridge"][-1]["is_total"]
        assert record["valuation_bridge"][-1]["value"] == pytest.approx(
            record["valuation"]["base"]
        )

    def test_excluded_methods_are_kept_with_their_reason(self, no_comparables):
        record = record_for(no_comparables)
        excluded = [item for item in record["methods"] if not item["included"]]
        assert excluded
        assert all(item["exclusion_reason"] for item in excluded)

    def test_included_method_weights_sum_to_one(self, record):
        total = sum(item["weight"] for item in record["methods"] if item["included"])
        assert total == pytest.approx(1.0)


class TestProvenance:
    def test_every_ledger_entry_names_its_source_type(self, record):
        assert record["source_ledger"]
        for entry in record["source_ledger"]:
            assert entry["source_type"] in config.SOURCE_TYPES
            assert entry["source_label"] == config.SOURCE_LABELS[entry["source_type"]]

    def test_assumptions_are_listed_separately(self, no_comparables):
        record = record_for(no_comparables)
        assert record["assumptions"]
        assumed = {
            entry["metric"]
            for entry in record["source_ledger"]
            if entry["source_type"] == config.ASSUMPTION
        }
        assert "revenue_multiple" in assumed

    def test_missing_comparables_are_stated_in_the_sources(self, no_comparables):
        record = record_for(no_comparables)
        assert any(
            item.get("reference") == config.NO_COMPARABLES for item in record["sources"]
        )

    def test_supplied_comparables_are_cited_individually(self, priced_with_comparables):
        record = record_for(priced_with_comparables)
        comparables = [
            item for item in record["sources"] if item["kind"] == "comparable_financing"
        ]
        assert len(comparables) == 4
        assert all(item["source"] for item in comparables)

    def test_missing_information_is_reported_not_filled(self, incomplete):
        incomplete.note_missing("Current revenue or ARR")
        record = record_for(incomplete)
        assert "Current revenue or ARR" in record["missing_information"]
        assert record["metrics"]["revenue"] is None
        assert record["metrics"]["arr"] is None

    def test_a_derived_arr_is_recorded_as_calculated(self, safe_round):
        from app.utils import normalization as norm

        arr, basis = norm.arr_from(None, safe_round.metrics.mrr)
        safe_round.metrics.arr = arr
        safe_round.ledger.record(
            "arr", arr, config.CALCULATED, "MRR x 12", config.MEDIUM, unit="$/year"
        )
        record = record_for(safe_round)
        entry = next(item for item in record["source_ledger"] if item["metric"] == "arr")
        assert entry["source_type"] == config.CALCULATED
        assert entry["source"] == "MRR x 12"


class TestValidationBlock:
    def test_findings_reach_the_record(self, overpriced):
        record = record_for(overpriced)
        assert record["validation"]["issues"]
        assert record["validation"]["error_count"] == 0

    def test_contradictions_are_carried_not_resolved(self, saas):
        saas.note_contradiction("Slide 4 says $2.4M ARR; slide 11 says $3.1M.")
        record = record_for(saas)
        assert saas.contradictions[0] in record["validation"]["contradictions"]
        assert any(
            issue["rule"] == "deck.contradiction" for issue in record["validation"]["issues"]
        )
