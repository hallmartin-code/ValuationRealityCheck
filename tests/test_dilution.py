"""Section 10's arithmetic, and the section 19 checks that police it.

    post-money         = pre-money + new capital
    investor ownership = new capital / post-money

Both identities are re-derived here from the reported figures rather than
recomputed from the same expression the engine used, so a change to one side
without the other fails the suite.
"""

from __future__ import annotations

import pytest

from app import config
from app.models.company import CompanyProfile, OperatingMetrics, RoundTerms
from app.models.valuation import DilutionCase, ValuationRange
from app.utils import validation
from app.valuation import dilution, valuation_engine

CASES = ("Requested", "Low", "Base", "High")


def analyse(profile: CompanyProfile):
    return valuation_engine.run(profile)


class TestIdentities:
    def test_post_money_is_pre_money_plus_the_raise(self, saas):
        analysis = analyse(saas)
        for case in analysis.dilution:
            assert case.post_money == pytest.approx(case.pre_money + saas.round.raise_amount)

    def test_ownership_is_the_raise_over_post_money(self, saas):
        analysis = analyse(saas)
        for case in analysis.dilution:
            assert case.new_investor_pct == pytest.approx(
                saas.round.raise_amount / case.post_money
            )

    def test_worked_example_from_the_standard(self):
        """$4M into a $24M pre-money is 14.3% of a $28M post-money."""
        case = dilution._case("Requested", 24_000_000, 4_000_000, "")
        assert case.post_money == pytest.approx(28_000_000)
        assert case.new_investor_pct == pytest.approx(0.142857, abs=1e-5)

    def test_the_second_worked_example(self):
        """$4M into an $18M pre-money is 18.2% of a $22M post-money."""
        case = dilution._case("Base", 18_000_000, 4_000_000, "")
        assert case.post_money == pytest.approx(22_000_000)
        assert case.new_investor_pct == pytest.approx(0.181818, abs=1e-5)

    def test_dilution_equals_new_investor_ownership(self, saas):
        """Existing holders give up exactly what the new investors take."""
        analysis = analyse(saas)
        for case in analysis.dilution:
            existing = 1.0 - (case.pre_money / case.post_money)
            assert existing == pytest.approx(case.new_investor_pct)


class TestOrdering:
    def test_a_lower_valuation_means_more_dilution(self, saas):
        analysis = analyse(saas)
        by_case = {case.label: case for case in analysis.dilution}
        assert (
            by_case["Low"].new_investor_pct
            > by_case["Base"].new_investor_pct
            > by_case["High"].new_investor_pct
        )

    def test_all_four_cases_are_priced(self, saas):
        analysis = analyse(saas)
        assert [case.label for case in analysis.dilution] == list(CASES)

    def test_the_requested_case_is_dropped_when_nothing_was_asked_for(self, incomplete):
        analysis = analyse(incomplete)
        assert [case.label for case in analysis.dilution] == ["Low", "Base", "High"]


class TestConvertibles:
    def test_a_safe_is_priced_at_its_cap(self, safe_round):
        analysis = analyse(safe_round)
        requested = analysis.dilution_case("Requested")
        assert requested.pre_money == pytest.approx(safe_round.round.valuation_cap)

    def test_the_note_says_conversion_is_not_settled(self, safe_round):
        analysis = analyse(safe_round)
        note = analysis.dilution_case("Requested").note.lower()
        assert "cap" in note
        assert "discount" in note

    def test_the_instrument_line_carries_the_terms(self, safe_round):
        line = dilution.instrument_note(safe_round)
        assert "SAFE" in line and "$15.0M cap" in line and "20% discount" in line

    def test_an_undisclosed_instrument_says_so(self, incomplete):
        incomplete.round.instrument = ""
        assert dilution.instrument_note(incomplete) == config.NOT_DISCLOSED


class TestCommitted:
    def test_the_committed_line_reconciles(self, saas):
        line = dilution.committed_note(saas)
        assert "$1.5M of $4.0M committed (38%)" in line
        assert "$2.5M remaining" in line

    def test_no_committed_capital_produces_no_line(self, safe_round):
        assert dilution.committed_note(safe_round) == ""


class TestReconciliation:
    def test_a_consistent_case_reconciles(self):
        assert dilution.reconciles(DilutionCase("Base", 18e6, 22e6, 4e6 / 22e6))

    def test_an_inconsistent_case_does_not(self):
        assert not dilution.reconciles(DilutionCase("Base", 18e6, 22e6, 0.30))

    def test_a_missing_figure_is_not_a_failure(self):
        assert dilution.reconciles(DilutionCase("Base", None, None, None))


class TestValidatorCatchesBadArithmetic:
    def test_a_broken_identity_is_reported(self, saas):
        analysis = analyse(saas)
        analysis.dilution[0].new_investor_pct = 0.99
        issues = validation.run(saas, analysis)
        assert any(issue.rule == "dilution.identity" for issue in issues)

    def test_ownership_outside_the_bounds_is_reported(self, saas):
        analysis = analyse(saas)
        analysis.dilution[0].pre_money = -1_000_000
        analysis.dilution[0].post_money = 3_000_000
        analysis.dilution[0].new_investor_pct = 1.33
        issues = validation.run(saas, analysis)
        assert any(issue.rule == "dilution.bounds" for issue in issues)

    def test_heavy_dilution_is_flagged_not_silently_accepted(self):
        profile = CompanyProfile(
            round=RoundTerms(raise_amount=5_000_000, requested_pre_money=4_000_000),
            metrics=OperatingMetrics(),
        )
        analysis = analyse(profile)
        issues = validation.run(profile, analysis)
        assert any(issue.rule == "dilution.heavy" for issue in issues)

    def test_a_non_positive_raise_is_an_error(self, saas):
        saas.round.raise_amount = 0
        issues = validation.run(saas, analyse(saas))
        assert any(
            issue.rule == "round.non_positive" and issue.severity == "ERROR"
            for issue in issues
        )

    def test_a_round_that_does_not_reconcile_is_an_error(self, saas):
        saas.round.requested_post_money = 30_000_000  # pre 18 + raise 4 = 22
        issues = validation.run(saas, analyse(saas))
        assert any(
            issue.rule == "round.identity" and issue.severity == "ERROR" for issue in issues
        )

    def test_committed_above_the_round_size_is_flagged(self, saas):
        saas.round.committed = 9_000_000
        issues = validation.run(saas, analyse(saas))
        assert any(issue.rule == "round.committed" for issue in issues)

    def test_an_implausible_percentage_is_an_error(self, saas):
        saas.metrics.gross_margin = 4.2
        issues = validation.run(saas, analyse(saas))
        assert any(issue.rule == "metric.range.gross_margin" for issue in issues)

    def test_a_thousandfold_units_misread_is_flagged(self, pre_revenue):
        """A $1.5M raise against $2,430 of revenue is usually a table denominated in thousands."""
        pre_revenue.metrics.revenue = 2_430
        issues = validation.run(pre_revenue, analyse(pre_revenue))
        flag = next(issue for issue in issues if issue.rule == "revenue.scale")
        assert "understated by that factor" in flag.message
        assert flag.severity == "WARNING"

    def test_plausible_revenue_is_not_flagged(self, saas):
        issues = validation.run(saas, analyse(saas))
        assert not any(issue.rule == "revenue.scale" for issue in issues)

    def test_a_genuinely_pre_revenue_company_is_not_flagged(self, life_science):
        issues = validation.run(life_science, analyse(life_science))
        assert not any(issue.rule == "revenue.scale" for issue in issues)

    def test_a_clean_analysis_raises_no_errors(self, priced_with_comparables):
        analysis = analyse(priced_with_comparables)
        issues = validation.run(priced_with_comparables, analysis)
        assert [issue for issue in issues if issue.severity == "ERROR"] == []


class TestValidatorRunsOnEveryScenario:
    SCENARIOS = (
        "pre_revenue",
        "saas",
        "hardware",
        "life_science",
        "concentrated",
        "overpriced",
        "safe_round",
        "priced_with_comparables",
        "incomplete",
    )

    @pytest.mark.parametrize("scenario", SCENARIOS)
    def test_no_scenario_produces_a_validation_error(self, scenario, request):
        """A scenario that fails its own validator is a bug in the engine, not the deck."""
        profile = request.getfixturevalue(scenario)
        analysis = analyse(profile)
        errors = [
            issue for issue in validation.run(profile, analysis) if issue.severity == "ERROR"
        ]
        assert errors == [], f"{scenario}: {[issue.message for issue in errors]}"
