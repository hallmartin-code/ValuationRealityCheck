"""The document template, checked against the code that is supposed to implement it.

`templates/valuation_report_template.json` describes the document: its zones,
sections, fields, input catalogue, validation rules and formatting. A template
nobody checks is documentation that quietly goes stale, so these tests treat it
as a contract — if the renderer grows a section, the extractor gains a field, or
a colour changes, the template has to move with it or the suite fails.

Nothing here asserts anything about a particular company. That is the point of
the file being tested.
"""

from __future__ import annotations

import json
import re

import pytest

from app import config
from app.extraction import company_extractor
from app.extraction import engine as extraction_engine
from app.reporting import pdf_report
from app.utils import validation
from app.valuation import valuation_engine

pdfplumber = pytest.importorskip("pdfplumber")

TEMPLATE_PATH = config.PROJECT_ROOT / "templates" / "valuation_report_template.json"


@pytest.fixture(scope="module")
def template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


class TestFileItself:
    def test_it_is_valid_json_with_the_expected_blocks(self, template):
        expected = {
            "template_name",
            "template_version",
            "description",
            "document",
            "provenance",
            "analysis_inputs",
            "valuation_methods",
            "fixed_fields",
            "zones",
            "sections",
            "fields",
            "validation",
            "rules",
            "reader_questions",
            "formatting",
        }
        assert expected <= set(template)

    def test_it_names_no_company(self, template):
        """A template that leaked a real company's details would be worse than none."""
        blob = json.dumps(template)
        for leaked in ("Northwind", "AccuBreath", "Halcyon", "Kestrel", "Ironvale", "Meridian"):
            assert leaked not in blob

    def test_no_field_carries_a_figure_instead_of_a_placeholder(self, template):
        """Every value on the page is filled at render time, so none may be baked in here.

        The formatting block is exempt and only that block: it exists to document
        what a rendered figure should look like, so it is the one place a worked
        example belongs.
        """
        described = {key: value for key, value in template.items() if key != "formatting"}
        for match in re.finditer(r'"[^"]*?\$\s?\d[^"]*?"', json.dumps(described)):
            assert "{{" in match.group(0), match.group(0)

    def test_every_field_carries_a_key_zone_and_type(self, template):
        for field in template["fields"]:
            assert field.get("key"), field
            assert field.get("zone") in {zone["id"] for zone in template["zones"]}
            assert field.get("type"), field["key"]

    def test_field_keys_are_unique(self, template):
        keys = [field["key"] for field in template["fields"]]
        assert len(keys) == len(set(keys))


class TestDocumentGeometry:
    def test_the_page_matches_the_renderer(self, template):
        document = template["document"]
        assert document["page_size"] == "letter"
        assert document["orientation"] == "portrait"
        assert document["margin_in"] == pytest.approx(config.PAGE_MARGIN)
        assert document["max_pages"] == 1

    def test_the_markers_match_the_configured_ones(self, template):
        document = template["document"]
        assert document["not_disclosed_marker"] == config.NOT_DISCLOSED
        assert document["insufficient_marker"] == config.INSUFFICIENT
        assert document["no_comparables_statement"] == config.NO_COMPARABLES

    def test_the_zone_heights_match_the_renderer(self, template):
        zones = {zone["id"]: zone for zone in template["zones"]}
        assert zones["header"]["height_in"] == pytest.approx(config.HEADER_HEIGHT)
        assert zones["footer"]["min_height_in"] * 72 == pytest.approx(pdf_report._MIN_FOOTER)
        assert zones["footer"]["max_height_in"] * 72 == pytest.approx(pdf_report._MAX_FOOTER)

    def test_the_output_patterns_match_what_main_writes(self, template, saas):
        from app import main

        pdf_path, json_path = main._paths(saas, None)
        assert pdf_path.name.endswith("_valuation.pdf")
        assert json_path.name.endswith("_valuation.json")
        assert template["document"]["filename_pattern"].endswith("_valuation.pdf")
        assert template["document"]["sidecar_pattern"].endswith("_valuation.json")


class TestProvenance:
    def test_the_source_types_match_the_configured_ones(self, template):
        declared = template["provenance"]["source_types"]
        assert [item["id"] for item in declared] == sorted(
            config.SOURCE_TYPES, key=lambda name: config.SOURCE_RANK[name]
        )

    def test_the_precedence_ranks_match(self, template):
        for item in template["provenance"]["source_types"]:
            assert item["rank"] == config.SOURCE_RANK[item["id"]]
            assert item["label"] == config.SOURCE_LABELS[item["id"]]

    def test_the_confidence_levels_match(self, template):
        assert template["provenance"]["confidence_levels"] == list(config.CONFIDENCE_LEVELS)


class TestAnalysisInputs:
    """The catalogue of what gets analyzed must be the catalogue the extractor asks for."""

    def test_the_groups_match_the_extraction_catalogue(self, template):
        declared = [group["id"] for group in template["analysis_inputs"]["groups"]]
        assert declared == list(extraction_engine.FIELDS)

    def test_every_extracted_field_is_documented(self, template):
        for group in template["analysis_inputs"]["groups"]:
            declared = [field["key"] for field in group["fields"]]
            assert declared == list(extraction_engine.FIELDS[group["id"]]), group["id"]

    def test_no_field_is_documented_that_is_never_extracted(self, template):
        documented = {
            f"{group['id']}.{field['key']}"
            for group in template["analysis_inputs"]["groups"]
            for field in group["fields"]
        }
        assert documented == set(extraction_engine.field_names())

    def test_every_documented_field_says_what_to_analyze(self, template):
        for group in template["analysis_inputs"]["groups"]:
            for field in group["fields"]:
                assert field["analyze"].strip(), f"{group['id']}.{field['key']}"

    def test_the_scorecard_factors_match(self, template):
        declared = [
            factor["key"] for factor in template["analysis_inputs"]["classification"]["factors"]
        ]
        assert declared == list(company_extractor.SCORECARD_FACTORS)

    def test_the_comparable_fields_match_what_is_parsed(self, template):
        from app.extraction import financing_extractor

        declared = template["analysis_inputs"]["externally_supplied"]["fields"]
        assert declared == list(financing_extractor.COMPARABLE_FIELDS)


class TestValuationMethods:
    def test_the_methods_match_the_engine(self, template):
        declared = [method["key"] for method in template["valuation_methods"]["methods"]]
        assert set(declared) == set(valuation_engine.RUNNERS)

    def test_the_stage_bands_match(self, template):
        assert template["valuation_methods"]["stage_bands"] == list(config.STAGES)

    def test_the_quality_adjusted_flag_matches_the_code(self, template, pre_revenue):
        """Only a method that prices quality itself may be held out of the adjustment."""
        analysis = valuation_engine.run(pre_revenue)
        declared = {
            method["key"]: method["quality_adjusted"]
            for method in template["valuation_methods"]["methods"]
        }
        for method in analysis.methods:
            assert declared[method.name] == method.quality_adjusted

    def test_every_method_documents_when_it_excludes_itself(self, template):
        for method in template["valuation_methods"]["methods"]:
            assert method["excluded_when"].strip(), method["key"]


class TestSectionsRender:
    @pytest.fixture
    def rendered_text(self, priced_with_comparables, tmp_path) -> str:
        """A rendered page to check the template against.

        The fullest scenario is used deliberately: every section populated, both
        driver columns filled and comparables cited, so a heading or column the
        template declares cannot pass by simply not being exercised.
        """
        analysis = valuation_engine.run(priced_with_comparables)
        analysis.issues = validation.run(priced_with_comparables, analysis)
        path = tmp_path / "conformance.pdf"
        pdf_report.render(priced_with_comparables, analysis, path)
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    def test_the_sections_are_numbered_from_one_without_gaps(self, template):
        numbers = [section["number"] for section in template["sections"]]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_every_declared_section_heading_appears_on_the_page(self, template, rendered_text):
        for section in template["sections"]:
            heading = f"{section['number']}. {section['heading']}".upper()
            assert heading in rendered_text, heading

    def test_the_page_carries_no_heading_the_template_omits(self, template, rendered_text):
        declared = {section["heading"].upper() for section in template["sections"]}
        found = set(re.findall(r"^(\d)\. ([A-Z][^\n]*)$", rendered_text, re.MULTILINE))
        assert {heading for _, heading in found} <= declared

    def test_every_section_key_is_used_by_at_least_one_field(self, template):
        used = {field.get("section") for field in template["fields"] if field.get("section")}
        declared = {section["key"] for section in template["sections"]}
        assert declared == used

    def test_the_declared_table_columns_appear_on_the_page(self, template, rendered_text):
        for field in template["fields"]:
            if field["type"] != "table" or "columns" not in field:
                continue
            for column in field["columns"]:
                assert column["label"] in rendered_text, f"{field['key']}.{column['label']}"

    def test_the_snapshot_declares_the_cell_count_it_renders(self, template, rendered_text):
        strip = next(f for f in template["fields"] if f["key"] == "snapshot_strip")
        assert strip["cells"] == 6

    def test_the_fixed_text_the_template_requires_is_present(self, template, rendered_text):
        assert config.FIRM_NAME in rendered_text
        assert config.CONFIDENTIALITY in rendered_text
        assert "Startup valuation is inherently subjective" in rendered_text


class TestValidationBlock:
    def test_every_rule_the_validator_can_emit_is_documented(self, template):
        source = (config.PROJECT_ROOT / "app" / "utils" / "validation.py").read_text(
            encoding="utf-8"
        )
        emitted = set(re.findall(r'"([a-z]+\.[a-z_]+(?:\.\{[a-z_]+\})?)"', source))
        documented = " ".join(template["validation"]["rules_checked"])
        for rule in emitted:
            # A parameterised rule is documented by its stem.
            stem = rule.split(".{")[0]
            assert stem in documented, rule

    def test_the_severities_match_the_validator(self, template):
        assert template["validation"]["severities"] == [
            validation.ERROR,
            validation.WARNING,
            validation.NOTE,
        ]


class TestFormatting:
    def test_the_palette_matches_the_configured_colours(self, template):
        palette = template["formatting"]["palette"]
        for name, value in palette.items():
            assert getattr(config, name.upper()) == value, name

    def test_the_status_colours_cover_every_status(self, template):
        declared = set(template["formatting"]["status_colours"])
        assert declared == set(config.STATUS_COLORS)

    def test_the_type_range_matches_the_render_profiles(self, template):
        low, high = template["formatting"]["typography"]["body_size_range_pt"]
        sizes = [profile[0] for profile in pdf_report._PROFILES]
        assert low == pytest.approx(min(sizes))
        assert high == pytest.approx(max(sizes))

    def test_the_declared_font_family_is_the_one_the_renderer_prefers(self, template):
        typography = template["formatting"]["typography"]
        assert typography["preferred_family"].replace(" ", "") == config.FONT_FAMILY
        assert typography["fallback_family"] == config.FALLBACK_FONTS[0]


class TestReaderQuestions:
    def test_all_ten_questions_are_present(self, template):
        assert len(template["reader_questions"]["questions"]) == 10

    def test_every_question_maps_to_a_declared_field(self, template):
        keys = {field["key"] for field in template["fields"]}
        for item in template["reader_questions"]["questions"]:
            for answered_by in item["answered_by"].split(","):
                assert answered_by.strip() in keys, item["question"]
