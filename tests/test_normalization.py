"""Reading deck text into numbers, and the rules that stop a misread reaching the page."""

from __future__ import annotations

import pytest

from app import config
from app.utils import normalization as norm


class TestParseMoney:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("$2.4M", 2_400_000.0),
            ("$750K", 750_000.0),
            ("$1.2B", 1_200_000_000.0),
            ("2.4 million", 2_400_000.0),
            ("$6.4 Millions", 6_400_000.0),
            ("1,250,000", 1_250_000.0),
            ("€3.5M", 3_500_000.0),
            (2_400_000, 2_400_000.0),
        ],
    )
    def test_reads_scale_and_currency(self, text, expected):
        assert norm.parse_money(text) == pytest.approx(expected)

    def test_prefers_the_currency_marked_figure(self):
        # The first number is a count, not money. The report must not print 38.
        assert norm.parse_money("38 customers, ACV $62,000") == pytest.approx(62_000.0)

    def test_a_bare_percentage_is_not_money(self):
        assert norm.parse_money("94% year over year") is None

    def test_missing_text_reads_as_missing(self):
        assert norm.parse_money("") is None
        assert norm.parse_money(None) is None

    @pytest.mark.parametrize(
        "text,expected",
        [("(1,377)", -1_377.0), ("($1.5M)", -1_500_000.0), ("(256)", -256.0)],
    )
    def test_accounting_parentheses_read_as_negative(self, text, expected):
        """A table writes a loss as (1,377); reading it as a profit inverts the company."""
        assert norm.parse_money(text) == pytest.approx(expected)

    def test_a_thousands_denominated_cell_scales(self):
        assert norm.parse_money("$2,430K") == pytest.approx(2_430_000.0)


class TestParsePercent:
    @pytest.mark.parametrize(
        "text,expected",
        [("94%", 0.94), ("0.94", 0.94), (0.76, 0.76), (76, 0.76), ("76 percent", 0.76)],
    )
    def test_reads_a_fraction(self, text, expected):
        assert norm.parse_percent(text) == pytest.approx(expected)

    def test_prefers_the_percent_marked_figure(self):
        assert norm.parse_percent("Device 50%; Consumable 87%") == pytest.approx(0.50)


class TestParseRetention:
    def test_retention_above_one_hundred_survives(self):
        # 112% NRR must not collapse to 1.0: expansion is the whole signal.
        assert norm.parse_retention("112%") == pytest.approx(1.12)
        assert norm.parse_retention(112) == pytest.approx(1.12)
        assert norm.parse_retention(1.12) == pytest.approx(1.12)

    def test_a_fraction_stays_a_fraction(self):
        assert norm.parse_retention("0.88") == pytest.approx(0.88)


class TestDurations:
    @pytest.mark.parametrize(
        "text,expected",
        [("18 months", 18), ("2 years", 24), ("3 quarters", 9), ("14", 14)],
    )
    def test_reads_months(self, text, expected):
        assert norm.parse_months(text) == pytest.approx(expected)


class TestCounts:
    def test_a_money_figure_is_not_a_count(self):
        assert norm.parse_count("$62,000") is None

    def test_a_percentage_is_not_a_count(self):
        assert norm.parse_count("31%") is None

    def test_reads_a_plain_count(self):
        assert norm.parse_count("38 customers") == pytest.approx(38.0)


class TestRecurringRevenue:
    def test_mrr_becomes_arr_and_says_so(self):
        arr, basis = norm.arr_from(None, 95_000)
        assert arr == pytest.approx(1_140_000.0)
        assert basis == "MRR x 12"

    def test_stated_arr_wins_over_mrr(self):
        arr, basis = norm.arr_from(2_400_000, 95_000)
        assert arr == pytest.approx(2_400_000.0)
        assert basis == "stated ARR"

    def test_neither_gives_nothing(self):
        assert norm.arr_from(None, None) == (None, "")

    @pytest.mark.parametrize(
        "period,expected", [("monthly", 1_200_000), ("quarterly", 400_000), ("annual", 100_000)]
    )
    def test_annualise(self, period, expected):
        assert norm.annualise(100_000, period) == pytest.approx(expected)


class TestRevenueClassification:
    """Section 3: pipeline and projections must never be counted as current revenue."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("2026 projected", norm.PROJECTED),
            ("qualified pipeline", norm.PIPELINE),
            ("signed contracts", norm.CONTRACTED),
            ("ARR run rate", norm.RUN_RATE),
            ("2025A actual", norm.CURRENT),
            ("TTM revenue", norm.HISTORICAL),
            ("potential at scale", norm.ASPIRATIONAL),
        ],
    )
    def test_classifies_the_label(self, label, expected):
        assert norm.classify_revenue_basis(label) == expected

    def test_pipeline_beats_projection_when_both_words_appear(self):
        # "2026 projected pipeline" is pipeline. Reading it as a projection would
        # still be wrong, but reading it as revenue would be far worse.
        assert norm.classify_revenue_basis("2026 projected pipeline") == norm.PIPELINE

    def test_unlabelled_is_treated_as_current(self):
        assert norm.classify_revenue_basis("") == norm.CURRENT

    def test_a_bare_future_year_is_a_forecast(self):
        """The commonest way a projection reaches a multiple: a model's column heading."""
        assert norm.classify_revenue_basis("2027", as_of_year=2026) == norm.PROJECTED
        assert norm.classify_revenue_basis("FY2029", as_of_year=2026) == norm.PROJECTED

    def test_a_past_or_current_year_stays_current(self):
        assert norm.classify_revenue_basis("2025A", as_of_year=2026) == norm.CURRENT
        assert norm.classify_revenue_basis("2026 actual", as_of_year=2026) == norm.CURRENT

    def test_an_explicit_word_still_beats_the_year(self):
        # "contracted 2028" is contracted revenue, not a forecast, but a bare
        # future year with no such word is.
        assert norm.classify_revenue_basis("2028 pipeline", as_of_year=2026) == norm.PIPELINE
        assert norm.classify_revenue_basis("2024 backlog", as_of_year=2026) == norm.CONTRACTED

    def test_the_year_rule_is_off_without_an_as_of_year(self):
        assert norm.classify_revenue_basis("2027") == norm.CURRENT

    @pytest.mark.parametrize(
        "basis,countable",
        [
            (norm.CURRENT, True),
            (norm.RUN_RATE, True),
            (norm.CONTRACTED, True),
            (norm.HISTORICAL, True),
            (norm.PROJECTED, False),
            (norm.PIPELINE, False),
            (norm.ASPIRATIONAL, False),
        ],
    )
    def test_only_earned_revenue_is_countable(self, basis, countable):
        assert norm.is_countable(basis) is countable


class TestCompleteRound:
    def test_derives_post_money(self):
        pre, post, raised = norm.complete_round(24_000_000, None, 6_000_000)
        assert post == pytest.approx(30_000_000)

    def test_derives_pre_money(self):
        pre, post, raised = norm.complete_round(None, 30_000_000, 6_000_000)
        assert pre == pytest.approx(24_000_000)

    def test_derives_the_raise(self):
        pre, post, raised = norm.complete_round(24_000_000, 30_000_000, None)
        assert raised == pytest.approx(6_000_000)

    def test_two_unknowns_stay_unknown(self):
        assert norm.complete_round(24_000_000, None, None) == (24_000_000, None, None)


class TestStageInference:
    def test_a_later_stage_claim_without_revenue_is_capped_at_seed(self):
        # Not accepted, not discarded: a Series A with nothing earned is priced
        # as a seed, and the revenue methods exclude themselves either way.
        assert norm.infer_stage("Series A", None, 1_000_000) == config.SEED
        assert norm.infer_stage("Series B", None, 1_000_000) == config.SEED

    def test_no_revenue_and_no_claim_is_pre_revenue(self):
        assert norm.infer_stage("Pre-seed", None, 1_000_000) == config.PRE_REVENUE
        assert norm.infer_stage("", None, None) == config.PRE_REVENUE

    def test_projected_revenue_never_lifts_the_band(self):
        # The caller passes countable revenue only, so a company whose only
        # revenue figure is a forecast arrives here as None.
        assert norm.infer_stage("Series A", None, 4_000_000) == config.SEED

    def test_series_a_revenue_reaches_the_series_a_band(self):
        assert norm.infer_stage("Series A", 2_400_000, 4_000_000) == config.SERIES_A

    def test_growth_revenue_reaches_the_growth_band(self):
        assert norm.infer_stage("Series B", 14_000_000, 20_000_000) == config.GROWTH

    def test_early_revenue_is_seed(self):
        assert norm.infer_stage("Seed", 600_000, 2_000_000) == config.SEED


class TestPercentiles:
    def test_median_of_an_even_sample_interpolates(self):
        assert norm.median([10.0, 20.0, 30.0, 40.0]) == pytest.approx(25.0)

    def test_quartiles_of_a_small_sample(self):
        values = [12_000_000.0, 19_000_000.0, 24_000_000.0, 32_000_000.0]
        assert norm.percentile(values, 0.25) == pytest.approx(17_250_000.0)
        assert norm.percentile(values, 0.75) == pytest.approx(26_000_000.0)

    def test_a_single_observation_is_its_own_percentile(self):
        assert norm.percentile([24_000_000.0], 0.25) == pytest.approx(24_000_000.0)

    def test_an_empty_sample_has_none(self):
        assert norm.percentile([], 0.5) is None


class TestSafeArithmetic:
    def test_division_by_zero_is_none_not_an_exception(self):
        assert norm.safe_div(4_000_000, 0) is None
        assert norm.safe_div(4_000_000, None) is None
        assert norm.safe_div(None, 4_000_000) is None

    def test_clamp(self):
        assert norm.clamp(1.4, 0.6, 1.2) == pytest.approx(1.2)
        assert norm.clamp(0.2, 0.6, 1.2) == pytest.approx(0.6)
