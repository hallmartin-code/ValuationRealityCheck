"""The valuation engine, across the ten scenarios section 20 asks the suite to cover.

The checks verify the arithmetic rather than the answer: that the blend equals
the weighted average of the methods that ran, that the bridge closes on the base
valuation, that no forbidden method ran, and that nothing was invented where
nothing was supplied.
"""

from __future__ import annotations

import pytest

from app import config
from app.models.company import Comparable, CompanyProfile, OperatingMetrics
from app.utils import normalization as norm
from app.valuation import adjustments, revenue_multiple, scorecard, valuation_engine
from app.valuation import comparable_financing as comps


def analyse(profile: CompanyProfile):
    return valuation_engine.run(profile)


# --- Range integrity, every scenario ----------------------------------------

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


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_produces_an_ordered_positive_range(scenario, request):
    analysis = analyse(request.getfixturevalue(scenario))
    assert analysis.range.base > 0
    assert analysis.range.low <= analysis.range.base <= analysis.range.high


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_included_method_weights_sum_to_one(scenario, request):
    analysis = analyse(request.getfixturevalue(scenario))
    total = sum(method.weight for method in analysis.used_methods)
    assert total == pytest.approx(1.0)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_blend_is_the_weighted_average_of_the_methods(scenario, request):
    """Re-derive the baseline a second way and confirm the engine agrees with itself."""
    analysis = analyse(request.getfixturevalue(scenario))
    expected = sum(method.base * method.weight for method in analysis.used_methods)
    assert analysis.baseline.base == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_bridge_closes_on_the_reported_base(scenario, request):
    """Section 8: the bridge must reconcile, not approximately reconcile."""
    analysis = analyse(request.getfixturevalue(scenario))
    assert analysis.bridge[-1].is_total
    assert analysis.bridge[-1].value == pytest.approx(analysis.range.base, rel=1e-6)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_bridge_lines_sum_to_the_bridge_total(scenario, request):
    analysis = analyse(request.getfixturevalue(scenario))
    lines = analysis.bridge
    baseline = lines[0].value
    moved = sum(line.pct or 0.0 for line in lines[1:-1])
    assert lines[-1].value == pytest.approx(baseline * (1 + moved), rel=1e-6)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_method_is_included_without_a_value(scenario, request):
    analysis = analyse(request.getfixturevalue(scenario))
    for method in analysis.used_methods:
        assert method.base is not None and method.base > 0
        assert method.basis, f"{method.name} was included without stating its basis"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_excluded_method_says_why(scenario, request):
    analysis = analyse(request.getfixturevalue(scenario))
    for method in analysis.excluded_methods:
        assert method.exclusion_reason


# --- 1. Pre-revenue seed company --------------------------------------------


class TestPreRevenue:
    def test_no_revenue_multiple_is_applied(self, pre_revenue):
        """Section 4: revenue multiples are forbidden without meaningful revenue."""
        analysis = analyse(pre_revenue)
        method = analysis.method(revenue_multiple.NAME)
        assert not method.included

    def test_the_scorecard_carries_the_valuation(self, pre_revenue):
        analysis = analyse(pre_revenue)
        assert analysis.method(scorecard.NAME).included

    def test_sensitivity_uses_milestone_probability_not_revenue(self, pre_revenue):
        analysis = analyse(pre_revenue)
        assert "Probability" in analysis.sensitivity.title
        assert analysis.sensitivity.column_labels == ["Low", "Base", "High"]

    def test_the_grid_centre_reproduces_the_base_valuation(self, pre_revenue):
        from app.utils import formatting as fmt

        analysis = analyse(pre_revenue)
        row, column = analysis.sensitivity.base_cell
        assert analysis.sensitivity.rows[row][1][column] == fmt.fmt_money(analysis.range.base)


# --- 2. SaaS with ARR and high growth ---------------------------------------


class TestSaaS:
    def test_the_revenue_multiple_runs_and_leads(self, saas):
        analysis = analyse(saas)
        method = analysis.method(revenue_multiple.NAME)
        assert method.included
        assert method.weight == max(m.weight for m in analysis.used_methods)

    def test_growth_and_retention_are_premiums(self, saas):
        analysis = analyse(saas)
        metrics = {item.metric for item in analysis.premiums()}
        assert "growth_rate" in metrics
        assert "nrr" in metrics

    def test_the_multiple_is_applied_to_arr(self, saas):
        analysis = analyse(saas)
        method = analysis.method(revenue_multiple.NAME)
        assert method.inputs["revenue"] == pytest.approx(saas.metrics.arr)
        assert method.base == pytest.approx(
            saas.metrics.arr * method.inputs["multiple_base"]
        )

    def test_the_grid_centre_reproduces_the_base_valuation(self, saas):
        from app.utils import formatting as fmt

        analysis = analyse(saas)
        row, column = analysis.sensitivity.base_cell
        assert analysis.sensitivity.rows[row][1][column] == fmt.fmt_money(analysis.range.base)


# --- 3. Hardware with a low gross margin ------------------------------------


class TestHardware:
    def test_a_low_margin_is_a_discount(self, hardware):
        analysis = analyse(hardware)
        margin = next(
            (item for item in analysis.discounts() if item.metric == "gross_margin"), None
        )
        assert margin is not None
        assert margin.pct == pytest.approx(
            min((config.MARGIN_REFERENCE - 0.34) * config.MARGIN_SENSITIVITY, config.MARGIN_CAP)
        )

    def test_granted_patents_are_a_premium(self, hardware):
        analysis = analyse(hardware)
        assert any(item.metric == "patents_granted" for item in analysis.premiums())

    def test_a_short_runway_and_long_cycle_are_discounts(self, hardware):
        analysis = analyse(hardware)
        metrics = {item.metric for item in analysis.discounts()}
        assert "runway_months" in metrics
        assert "sales_cycle_months" in metrics

    def test_the_net_adjustment_is_negative(self, hardware):
        analysis = analyse(hardware)
        assert analysis.range.base < analysis.baseline.base


# --- 4. Life science awaiting approval --------------------------------------


class TestLifeScience:
    def test_a_pending_approval_is_a_discount(self, life_science):
        analysis = analyse(life_science)
        assert any(item.metric == "regulatory_status" for item in analysis.discounts())

    def test_the_cap_stands_in_for_a_requested_pre_money(self, life_science):
        analysis = analyse(life_science)
        assert analysis.requested_basis == "valuation cap"
        assert analysis.requested_valuation == pytest.approx(12_000_000)

    def test_the_vc_method_grows_from_som_and_labels_the_assumption(self, life_science):
        analysis = analyse(life_science)
        method = analysis.method("venture_method")
        assert method.included
        assert "SOM" in method.inputs["exit_revenue_basis"]
        assert method.inputs["assumptions"]

    def test_the_scorecard_is_held_out_of_the_adjustment(self, life_science):
        """A quality-adjusted method must not be adjusted for quality twice."""
        analysis = analyse(life_science)
        card = analysis.method(scorecard.NAME)
        assert card.included and card.quality_adjusted
        expected = 1.0 - card.weight
        assert analysis.adjustment_applied_share == pytest.approx(expected)


# --- 5. High customer concentration -----------------------------------------


class TestConcentration:
    def test_the_top_band_discount_applies(self, concentrated):
        analysis = analyse(concentrated)
        item = next(
            item for item in analysis.discounts() if item.metric == "largest_customer_pct"
        )
        assert item.pct == pytest.approx(config.CONCENTRATION_BANDS[0][1])

    def test_low_concentration_is_a_premium_instead(self, saas):
        analysis = analyse(saas)
        item = next(
            item for item in analysis.premiums() if item.metric == "largest_customer_pct"
        )
        assert item.pct == pytest.approx(config.ADJUSTMENT_STEP["mild"])

    def test_concentration_appears_among_the_decision_factors(self, concentrated):
        analysis = analyse(concentrated)
        assert any("concentration" in factor.lower() for factor in analysis.decision_factors)


# --- 6. Requested valuation materially above the range ----------------------


class TestRequestedGap:
    def test_a_large_premium_is_classified_a_material_gap(self, overpriced):
        analysis = analyse(overpriced)
        assert analysis.requested_status == config.MATERIAL_GAP
        assert analysis.requested_gap_percent > config.MATERIAL_GAP_THRESHOLD

    def test_the_gap_reconciles_with_the_base(self, overpriced):
        analysis = analyse(overpriced)
        assert analysis.requested_gap_amount == pytest.approx(
            analysis.requested_valuation - analysis.range.base
        )
        assert analysis.requested_gap_percent == pytest.approx(
            analysis.requested_gap_amount / analysis.range.base
        )

    def test_an_ask_inside_the_band_is_defensible(self, saas):
        analysis = analyse(saas)
        saas.round.requested_pre_money = analysis.range.base
        assert analyse(saas).requested_status == config.WITHIN_RANGE

    def test_an_ask_below_the_band_is_below_market(self, saas):
        analysis = analyse(saas)
        saas.round.requested_pre_money = analysis.range.low * 0.5
        assert analyse(saas).requested_status == config.BELOW_RANGE

    def test_a_high_ask_does_not_discount_the_estimate(self, saas):
        """The gap must be an independent measurement, not a self-inflicted one."""
        modest = analyse(saas).range.base
        saas.round.requested_pre_money = 200_000_000
        assert analyse(saas).range.base == pytest.approx(modest)

    def test_no_ask_is_reported_as_insufficient_not_guessed(self, incomplete):
        analysis = analyse(incomplete)
        assert analysis.requested_valuation is None
        assert analysis.requested_status == config.INSUFFICIENT


# --- 7. No comparable financing data ----------------------------------------


class TestNoComparables:
    def test_the_method_excludes_itself_with_the_specified_wording(self, no_comparables):
        analysis = analyse(no_comparables)
        method = analysis.method(comps.NAME)
        assert not method.included
        assert method.exclusion_reason == config.NO_COMPARABLES

    def test_the_report_states_that_none_were_supplied(self, no_comparables):
        assert analyse(no_comparables).comparable_note == config.NO_COMPARABLES

    def test_nothing_is_invented_to_fill_the_gap(self, no_comparables):
        analysis = analyse(no_comparables)
        assert not any(
            entry.source_type == config.COMPARABLE
            for entry in no_comparables.ledger.entries
            if entry.metric in ("revenue_multiple", "comparable_baseline")
        )

    def test_the_fallback_multiple_is_labelled_an_assumption(self, no_comparables):
        analysis = analyse(no_comparables)
        entry = no_comparables.ledger.get("revenue_multiple")
        assert entry.source_type == config.ASSUMPTION
        assert any("revenue multiple" in item for item in analysis.assumptions)


# --- 8 and 9. SAFE and priced equity ----------------------------------------


class TestInstruments:
    def test_a_safe_is_priced_off_its_cap_and_says_so(self, safe_round):
        analysis = analyse(safe_round)
        assert safe_round.round.is_convertible
        assert analysis.requested_basis == "valuation cap"
        requested = analysis.dilution_case("Requested")
        assert "cap" in requested.note.lower()

    def test_mrr_only_still_produces_an_arr_multiple(self, safe_round):
        """The engine values the derived ARR, not the monthly figure."""
        safe_round.metrics.arr, _ = norm.arr_from(None, safe_round.metrics.mrr)
        analysis = analyse(safe_round)
        method = analysis.method(revenue_multiple.NAME)
        assert method.included
        assert method.inputs["revenue"] == pytest.approx(safe_round.metrics.mrr * 12)

    def test_a_priced_round_needs_no_cap_note(self, saas):
        analysis = analyse(saas)
        assert not saas.round.is_convertible
        assert analysis.dilution_case("Requested").note == ""

    def test_comparables_anchor_the_multiple_when_supplied(self, priced_with_comparables):
        analysis = analyse(priced_with_comparables)
        entry = priced_with_comparables.ledger.get("revenue_multiple")
        assert entry.source_type == config.COMPARABLE
        assert analysis.method(comps.NAME).included

    def test_comparables_take_the_weight_bonus(self, priced_with_comparables, saas):
        with_comps = analyse(priced_with_comparables).method(comps.NAME).weight
        nominal = config.METHOD_WEIGHTS[config.SERIES_A][comps.NAME]
        assert with_comps > nominal

    def test_the_comparable_median_is_the_median_of_the_supplied_rows(
        self, priced_with_comparables
    ):
        analysis = analyse(priced_with_comparables)
        method = analysis.method(comps.NAME)
        observed = [item.pre_money for item in priced_with_comparables.usable_comparables]
        assert method.inputs["reading"] == "pre-money percentile"
        assert method.base == pytest.approx(norm.median(observed))
        assert method.low == pytest.approx(norm.percentile(observed, 0.25))
        assert method.high == pytest.approx(norm.percentile(observed, 0.75))

    def test_the_two_comp_readings_stay_distinct(self, priced_with_comparables):
        """The multiple method and the comparable method must not be one calculation twice.

        Both draw on the same rows, but one scales the company's own revenue by a
        market multiple and the other reports what those companies actually raised
        at. Reporting the same number twice would agree with itself at a combined
        weight of eighty per cent and collapse the range to a false precision.
        """
        analysis = analyse(priced_with_comparables)
        multiple = analysis.method(revenue_multiple.NAME)
        comparable = analysis.method(comps.NAME)
        assert multiple.included and comparable.included
        assert comparable.inputs["reading"] == "pre-money percentile"
        assert "multiple_base" in multiple.inputs
        assert "multiple_median" not in comparable.inputs

    def test_the_range_is_not_collapsed_by_agreeing_methods(self, priced_with_comparables):
        analysis = analyse(priced_with_comparables)
        spread = (analysis.range.high - analysis.range.low) / analysis.range.base
        assert spread > config.MIN_RANGE_SPREAD

    def test_the_size_difference_from_the_peer_set_is_stated(self, priced_with_comparables):
        analysis = analyse(priced_with_comparables)
        assert "peer median" in analysis.method(comps.NAME).detail


# --- 10. Incomplete deck ----------------------------------------------------


class TestIncomplete:
    def test_an_almost_empty_deck_still_produces_a_defensible_answer(self, incomplete):
        analysis = analyse(incomplete)
        assert analysis.used_methods
        assert analysis.range.base > 0

    def test_confidence_is_low_and_says_why(self, incomplete):
        analysis = analyse(incomplete)
        assert analysis.confidence == config.LOW
        assert "no countable revenue" in analysis.confidence_reason.lower()

    def test_the_answer_is_visibly_built_on_assumptions(self, incomplete):
        analysis = analyse(incomplete)
        assert analysis.assumptions
        assert all(
            entry.source_type == config.ASSUMPTION
            for entry in incomplete.ledger.assumptions()
        )


# --- Cross-cutting rules ----------------------------------------------------


class TestSourcePrecedence:
    def test_a_user_value_outranks_a_deck_value(self, saas):
        saas.ledger.record("growth_rate", 0.94, config.DECK, "slide 8")
        saas.ledger.record("growth_rate", 0.71, config.USER, "user input")
        assert saas.ledger.number("growth_rate") == pytest.approx(0.71)
        assert saas.ledger.get("growth_rate").source_type == config.USER

    def test_an_assumption_cannot_overwrite_a_deck_value(self, saas):
        saas.ledger.record("gross_margin", 0.76, config.DECK, "slide 9")
        saas.ledger.record("gross_margin", 0.70, config.ASSUMPTION, "sector default")
        assert saas.ledger.number("gross_margin") == pytest.approx(0.76)
        assert saas.ledger.superseded

    def test_the_full_order_holds(self, saas):
        for source in (config.ASSUMPTION, config.CALCULATED, config.COMPARABLE, config.DECK):
            saas.ledger.record("nrr", 1.0, source, "test")
        saas.ledger.record("nrr", 1.12, config.USER, "user input")
        assert saas.ledger.get("nrr").source_type == config.USER


class TestProjectedRevenue:
    def test_a_projection_never_reaches_a_multiple(self, saas):
        """Section 3: the single most consequential rule in the standard."""
        saas.metrics.arr = None
        saas.metrics.revenue = 9_000_000
        saas.metrics.revenue_basis = norm.PROJECTED
        analysis = analyse(saas)
        assert saas.metrics.countable_revenue is None
        assert not analysis.method(revenue_multiple.NAME).included

    def test_pipeline_is_not_revenue(self, saas):
        saas.metrics.arr = None
        saas.metrics.revenue = 40_000_000
        saas.metrics.revenue_basis = norm.PIPELINE
        assert saas.metrics.countable_revenue is None

    def test_contracted_revenue_is_countable(self, saas):
        saas.metrics.arr = None
        saas.metrics.revenue = 2_400_000
        saas.metrics.revenue_basis = norm.CONTRACTED
        assert analyse(saas).method(revenue_multiple.NAME).included


class TestAdjustmentDiscipline:
    def test_an_undisclosed_metric_produces_no_factor(self):
        """Silence is not a weakness and not a strength."""
        blank = CompanyProfile()
        blank.metrics = OperatingMetrics()
        metrics = {item.metric for item in adjustments.build(blank)}
        assert "nrr" not in metrics
        assert "gross_margin" not in metrics
        assert "largest_customer_pct" not in metrics

    def test_the_net_adjustment_is_clamped(self, saas):
        saas.metrics.growth_rate = 40.0
        saas.metrics.nrr = 2.5
        saas.metrics.gross_margin = 0.99
        net = adjustments.net_adjustment(adjustments.build(saas))
        assert net <= config.NET_ADJUSTMENT_BAND[1]

    def test_a_clamped_net_still_reconciles_in_the_bridge(self, saas):
        saas.metrics.growth_rate = 40.0
        saas.metrics.nrr = 2.5
        analysis = analyse(saas)
        assert analysis.bridge[-1].value == pytest.approx(analysis.range.base, rel=1e-6)

    def test_every_factor_carries_a_rationale(self, hardware):
        for item in adjustments.build(hardware):
            assert item.rationale, f"{item.label} was applied without a stated rationale"


class TestConfidence:
    def test_evidence_raises_confidence(self, priced_with_comparables, no_comparables):
        with_evidence = analyse(priced_with_comparables).confidence
        without = analyse(no_comparables).confidence
        levels = {config.LOW: 0, config.MEDIUM: 1, config.HIGH: 2}
        assert levels[with_evidence] >= levels[without]

    def test_confidence_is_always_one_of_the_three_levels(self, request):
        for scenario in SCENARIOS:
            analysis = analyse(request.getfixturevalue(scenario))
            assert analysis.confidence in config.CONFIDENCE_LEVELS
            assert analysis.confidence_reason
