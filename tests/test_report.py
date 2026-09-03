"""The PDF: one page, every scenario, with the decisive figures actually on it.

The one-page constraint in section 14 is the reason the renderer retries at
tighter type profiles, so it is worth testing rather than trusting. The text
assertions extract the rendered page and check that the numbers an investor is
promised in section 21 are present — a layout that silently drops the ask or the
verdict would otherwise pass every other test in the suite.
"""

from __future__ import annotations

import pytest

from app import config
from app.reporting import pdf_report
from app.utils import formatting as fmt
from app.utils import validation
from app.valuation import valuation_engine

pdfplumber = pytest.importorskip("pdfplumber")

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


def render(profile, tmp_path, name="report.pdf"):
    analysis = valuation_engine.run(profile)
    analysis.issues = validation.run(profile, analysis)
    result = pdf_report.render(profile, analysis, tmp_path / name)
    return analysis, result


def text_of(path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


class TestOnePage:
    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_every_scenario_fits_on_one_page(self, scenario, request, tmp_path):
        _, result = render(request.getfixturevalue(scenario), tmp_path, f"{scenario}.pdf")
        assert result["pages"] == 1

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_type_stays_readable(self, scenario, request, tmp_path):
        """Section 15: reduce content before dropping type below a readable size."""
        _, result = render(request.getfixturevalue(scenario), tmp_path, f"{scenario}.pdf")
        body_size = pdf_report._PROFILES[result["profile"]][0]
        assert body_size >= 7.2

    def test_a_long_company_name_does_not_break_the_header(self, saas, tmp_path):
        saas.identity.name = "Northwind Analytics International Holdings Corporation Limited"
        _, result = render(saas, tmp_path)
        assert result["pages"] == 1

    def test_a_crowded_profile_still_fits(self, hardware, tmp_path):
        """Every optional line present at once is the worst case for the layout."""
        hardware.missing_information = [f"Missing item {index}" for index in range(12)]
        hardware.contradictions = [
            "Slide 4 says one thing and slide 11 says another, at some length.",
            "The round size on the cover does not match the ask slide.",
        ]
        hardware.round.committed = 2_000_000
        _, result = render(hardware, tmp_path)
        assert result["pages"] == 1


class TestPageContent:
    @pytest.fixture
    def rendered(self, saas, tmp_path):
        analysis, result = render(saas, tmp_path)
        return analysis, text_of(result["path"])

    def test_the_six_sections_are_all_present(self, rendered):
        _, text = rendered
        for heading in (
            "COMPANY SNAPSHOT",
            "VALUATION RANGE",
            "VALUATION METHODS",
            "PREMIUM / DISCOUNT DRIVERS",
            "ROUND & DILUTION",
            "SENSITIVITY & KEY MILESTONE",
        ):
            assert heading in text

    def test_the_header_identifies_the_company(self, rendered):
        _, text = rendered
        assert "NORTHWIND ANALYTICS" in text
        assert "TEN CAPITAL GROUP" in text
        assert "B2B SaaS" in text

    def test_the_range_is_on_the_page(self, rendered):
        analysis, text = rendered
        for value in (analysis.range.low, analysis.range.base, analysis.range.high):
            assert fmt.fmt_money(value) in text

    def test_the_ask_and_the_verdict_are_on_the_page(self, rendered):
        analysis, text = rendered
        assert fmt.fmt_money(analysis.requested_valuation) in text
        assert analysis.requested_status in text
        assert fmt.fmt_signed_pct(analysis.requested_gap_percent) in text

    def test_the_methods_and_their_weights_are_shown(self, rendered):
        analysis, text = rendered
        for method in analysis.used_methods:
            assert fmt.method_label(method.name) in text
            assert fmt.fmt_pct(method.weight) in text

    def test_the_bridge_reconciles_on_the_page(self, rendered):
        analysis, text = rendered
        assert "Bridge:" in text
        assert fmt.fmt_money(analysis.baseline.base) in text

    def test_the_dilution_cases_are_shown(self, rendered):
        analysis, text = rendered
        for case in analysis.dilution:
            assert case.label in text
            assert fmt.fmt_pct(case.new_investor_pct, 1) in text

    def test_confidence_and_the_disclaimer_are_in_the_footer(self, rendered):
        analysis, text = rendered
        assert f"VALUATION CONFIDENCE: {analysis.confidence}" in text
        assert "Startup valuation is inherently subjective" in text
        assert config.CONFIDENTIALITY in text

    def test_the_milestone_is_stated(self, rendered):
        analysis, text = rendered
        assert analysis.key_milestone.split(" — ")[0][:30] in text


class TestHonestyOnThePage:
    def test_missing_comparables_are_stated_not_omitted(self, no_comparables, tmp_path):
        _, result = render(no_comparables, tmp_path)
        assert config.NO_COMPARABLES in text_of(result["path"])

    def test_a_pre_revenue_company_says_so_rather_than_showing_a_zero(
        self, pre_revenue, tmp_path
    ):
        _, result = render(pre_revenue, tmp_path)
        assert "Pre-revenue" in text_of(result["path"])

    def test_undisclosed_metrics_appear_in_the_footer(self, incomplete, tmp_path):
        incomplete.note_missing("Net revenue retention")
        _, result = render(incomplete, tmp_path)
        text = text_of(result["path"])
        assert "NOT DISCLOSED" in text
        assert "Net revenue retention" in text

    def test_a_safe_states_that_it_is_priced_at_the_cap(self, safe_round, tmp_path):
        _, result = render(safe_round, tmp_path)
        text = text_of(result["path"])
        assert "cap" in text.lower()
        assert "SAFE" in text

    def test_a_scorecard_only_run_lists_drivers_without_zero_percentages(
        self, incomplete, tmp_path
    ):
        """Every factor printing "+0%" reads as a fault, not as an absence of double counting."""
        analysis, result = render(incomplete, tmp_path)
        assert analysis.adjustment_applied_share == pytest.approx(0.0)
        assert analysis.adjustments, "the scenario needs drivers for this test to mean anything"
        text = text_of(result["path"])
        assert "+0%" not in text
        assert "−0%" not in text and "-0%" not in text
        assert "already prices these factors" in text

    def test_a_blended_run_does_show_the_percentages(self, saas, tmp_path):
        analysis, result = render(saas, tmp_path)
        assert analysis.adjustment_applied_share == pytest.approx(1.0)
        text = text_of(result["path"])
        largest = analysis.premiums()[0]
        assert f"+{largest.pct * 100:.0f}%" in text

    def test_supplied_comparables_are_cited_on_the_page(
        self, priced_with_comparables, tmp_path
    ):
        _, result = render(priced_with_comparables, tmp_path)
        text = text_of(result["path"])
        assert "Example Analytics Co" in text
        assert "PitchBook" in text
