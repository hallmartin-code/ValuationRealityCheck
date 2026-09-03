"""Section 19: the checks that run before anything is rendered.

Two kinds of check live here. Identity checks re-derive a number a second way and
confirm the analysis agrees with itself — pre + raise = post, ownership = raise /
post, the bridge summing to the base. Coherence checks ask whether the result is
possible at all: a negative valuation, a margin above 100%, a range whose base
sits outside its own bounds.

A failure is reported. Nothing here edits a value to make a check pass, and
nothing here resolves a contradiction the deck itself contains.
"""

from __future__ import annotations

from .. import config
from ..models.company import CompanyProfile
from ..models.valuation import ValidationIssue, ValuationAnalysis
from ..utils import formatting as fmt
from ..utils import normalization as norm
from ..valuation import dilution as dilution_model

ERROR, WARNING, NOTE = "ERROR", "WARNING", "NOTE"

# Identity checks compare two floating-point paths to the same number.
TOLERANCE = 0.005

# Plausible bounds for the percentage inputs, as fractions.
BOUNDS: dict[str, tuple[float, float]] = {
    "gross_margin": (-1.0, 1.0),
    "growth_rate": (-1.0, 20.0),
    "largest_customer_pct": (0.0, 1.0),
    "nrr": (0.0, 3.0),
    "gross_retention": (0.0, 1.0),
    "retention": (0.0, 1.0),
    "churn": (0.0, 1.0),
    "discount_rate": (0.0, 1.0),
}


def run(profile: CompanyProfile, analysis: ValuationAnalysis) -> list[ValidationIssue]:
    """Run every check and return the findings, most severe first."""
    issues: list[ValidationIssue] = []
    issues += _round(profile)
    issues += _percentages(profile)
    issues += _revenue(profile)
    issues += _range(analysis)
    issues += _methods(analysis)
    issues += _bridge(analysis)
    issues += _dilution(analysis)
    issues += _requested(analysis, profile)
    issues += _consent(profile)
    issues += _contradictions(profile)

    order = {ERROR: 0, WARNING: 1, NOTE: 2}
    return sorted(issues, key=lambda issue: order.get(issue.severity, 3))


# --- Inputs -----------------------------------------------------------------


def _round(profile: CompanyProfile) -> list[ValidationIssue]:
    """Round size, and the pre + raise = post identity."""
    issues: list[ValidationIssue] = []
    terms = profile.round

    if terms.raise_amount is None:
        issues.append(
            ValidationIssue(
                WARNING,
                "round.missing",
                "No round size was disclosed, so dilution cannot be computed.",
            )
        )
    elif terms.raise_amount <= 0:
        issues.append(
            ValidationIssue(
                ERROR, "round.non_positive", "The round size must be greater than zero."
            )
        )

    pre, post, raised = (
        terms.requested_pre_money,
        terms.requested_post_money,
        terms.raise_amount,
    )
    if None not in (pre, post, raised) and post:
        if abs((pre + raised) - post) / abs(post) > 0.02:
            issues.append(
                ValidationIssue(
                    ERROR,
                    "round.identity",
                    f"Pre-money {fmt.fmt_money(pre)} plus raise {fmt.fmt_money(raised)} is "
                    f"{fmt.fmt_money(pre + raised)}, not the stated post-money "
                    f"{fmt.fmt_money(post)}.",
                )
            )

    if terms.committed is not None and raised and terms.committed > raised * 1.001:
        issues.append(
            ValidationIssue(
                WARNING,
                "round.committed",
                f"Committed capital {fmt.fmt_money(terms.committed)} exceeds the stated round "
                f"size {fmt.fmt_money(raised)}.",
            )
        )

    if terms.is_convertible and terms.valuation_cap is None:
        issues.append(
            ValidationIssue(
                WARNING,
                "round.no_cap",
                f"The instrument is a {terms.instrument} but no valuation cap was disclosed, "
                "so the conversion economics cannot be modelled.",
            )
        )
    return issues


def _percentages(profile: CompanyProfile) -> list[ValidationIssue]:
    """Every percentage input inside a plausible range."""
    issues: list[ValidationIssue] = []
    for name, (low, high) in BOUNDS.items():
        value = getattr(profile.metrics, name, None)
        if value is None:
            value = getattr(profile.round, name, None)
        if value is None:
            continue
        if not low <= value <= high:
            issues.append(
                ValidationIssue(
                    ERROR,
                    f"metric.range.{name}",
                    f"{name.replace('_', ' ')} of {fmt.fmt_pct(value, 1)} falls outside the "
                    f"plausible range {fmt.fmt_pct(low)} to {fmt.fmt_pct(high)}.",
                )
            )
    return issues


def _revenue(profile: CompanyProfile) -> list[ValidationIssue]:
    """No projected or pipeline figure may have reached the multiples."""
    issues: list[ValidationIssue] = []
    metrics = profile.metrics

    if metrics.revenue is not None and not norm.is_countable(metrics.revenue_basis):
        issues.append(
            ValidationIssue(
                NOTE,
                "revenue.basis",
                f"Revenue of {fmt.fmt_money(metrics.revenue)} is classified "
                f"{metrics.revenue_basis} and is excluded from every revenue multiple.",
            )
        )
        if metrics.countable_revenue is not None and metrics.arr is None:
            issues.append(
                ValidationIssue(
                    ERROR,
                    "revenue.leak",
                    "A non-current revenue figure reached the countable revenue used by the "
                    "multiples.",
                )
            )

    countable = metrics.countable_revenue
    if (
        countable is not None
        and 0 < countable < config.MEANINGFUL_REVENUE_FLOOR
        and (profile.round.raise_amount or 0) > 100 * countable
    ):
        # A deck raising millions against a few thousand dollars of revenue is
        # usually a units problem: financial tables are routinely denominated in
        # thousands, and a figure lifted from one without its header is understated
        # a thousandfold. The methods already exclude it; the reader needs to know
        # why the number on the page looks the way it does.
        issues.append(
            ValidationIssue(
                WARNING,
                "revenue.scale",
                f"Revenue reads as {fmt.fmt_money_exact(countable)} against a "
                f"{fmt.fmt_money(profile.round.raise_amount)} raise. If it was taken from a "
                "table denominated in thousands or millions, it is understated by that factor. "
                "Confirm the figure and its units before relying on this range.",
            )
        )

    if metrics.arr is not None and metrics.mrr is not None:
        implied = metrics.mrr * 12
        if implied and abs(metrics.arr - implied) / implied > 0.05:
            issues.append(
                ValidationIssue(
                    WARNING,
                    "revenue.arr_mrr",
                    f"Stated ARR {fmt.fmt_money(metrics.arr)} does not reconcile with stated "
                    f"MRR of {fmt.fmt_money(metrics.mrr)} ({fmt.fmt_money(implied)} annualised).",
                )
            )

    if metrics.pipeline is not None and metrics.countable_revenue is not None:
        if abs(metrics.pipeline - metrics.countable_revenue) < 1.0:
            issues.append(
                ValidationIssue(
                    WARNING,
                    "revenue.pipeline_equals_revenue",
                    "The pipeline figure and the revenue figure are identical, which usually "
                    "means one of the two was misread from the deck.",
                )
            )
    return issues


# --- Outputs ----------------------------------------------------------------


def _range(analysis: ValuationAnalysis) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    valuation = analysis.range

    if valuation.base <= 0:
        issues.append(
            ValidationIssue(
                ERROR,
                "valuation.non_positive",
                "The estimated base valuation is not a positive number.",
            )
        )
        return issues

    if not valuation.low <= valuation.base <= valuation.high:
        issues.append(
            ValidationIssue(
                ERROR,
                "valuation.order",
                f"The range is out of order: low {fmt.fmt_money(valuation.low)}, base "
                f"{fmt.fmt_money(valuation.base)}, high {fmt.fmt_money(valuation.high)}.",
            )
        )

    spread = (valuation.high - valuation.low) / valuation.base
    if spread > 1.5:
        issues.append(
            ValidationIssue(
                WARNING,
                "valuation.spread",
                f"The range spans {fmt.fmt_pct(spread)} of the base valuation, which is wide "
                "enough that the methods disagree materially.",
            )
        )
    return issues


def _methods(analysis: ValuationAnalysis) -> list[ValidationIssue]:
    """Weights sum to one, and every included method produced a usable number."""
    issues: list[ValidationIssue] = []
    included = analysis.used_methods

    if not included:
        issues.append(
            ValidationIssue(
                ERROR,
                "methods.none",
                "No valuation method could be grounded in the information supplied.",
            )
        )
        return issues

    total = sum(method.weight for method in included)
    if abs(total - 1.0) > TOLERANCE:
        issues.append(
            ValidationIssue(
                ERROR,
                "methods.weights",
                f"Method weights sum to {total:.3f} rather than 1.000.",
            )
        )

    for method in included:
        if method.base is None or method.base <= 0:
            issues.append(
                ValidationIssue(
                    ERROR,
                    "methods.value",
                    f"{fmt.method_label(method.name)} is included but produced no positive value.",
                )
            )
    return issues


def _bridge(analysis: ValuationAnalysis) -> list[ValidationIssue]:
    """The bridge's last line must equal the base valuation it claims to reach."""
    if not analysis.bridge:
        return []
    total = analysis.bridge[-1].value
    if analysis.range.base <= 0:
        return []
    if abs(total - analysis.range.base) / analysis.range.base > 0.02:
        return [
            ValidationIssue(
                ERROR,
                "bridge.total",
                f"The bridge closes at {fmt.fmt_money(total)} but the reported base valuation "
                f"is {fmt.fmt_money(analysis.range.base)}.",
            )
        ]
    return []


def _dilution(analysis: ValuationAnalysis) -> list[ValidationIssue]:
    """Each dilution case reconciles, and ownership stays inside 0-100%."""
    issues: list[ValidationIssue] = []
    for case in analysis.dilution:
        if not dilution_model.reconciles(case):
            issues.append(
                ValidationIssue(
                    ERROR,
                    "dilution.identity",
                    f"{case.label} case: ownership does not equal new capital over post-money.",
                )
            )
        if case.new_investor_pct is not None and not 0.0 < case.new_investor_pct < 1.0:
            issues.append(
                ValidationIssue(
                    ERROR,
                    "dilution.bounds",
                    f"{case.label} case implies {fmt.fmt_pct(case.new_investor_pct)} for new "
                    "investors, which is outside 0-100%.",
                )
            )
        elif case.new_investor_pct is not None and case.new_investor_pct > 0.40:
            issues.append(
                ValidationIssue(
                    WARNING,
                    "dilution.heavy",
                    f"{case.label} case dilutes existing holders by "
                    f"{fmt.fmt_pct(case.new_investor_pct)} in a single round.",
                )
            )
    return issues


def _requested(analysis: ValuationAnalysis, profile: CompanyProfile) -> list[ValidationIssue]:
    """The requested valuation is identified, and flagged when it is far above the range."""
    if analysis.requested_valuation is None:
        return [
            ValidationIssue(
                WARNING,
                "requested.missing",
                "Neither a requested pre-money valuation nor a valuation cap was disclosed.",
            )
        ]
    if analysis.requested_status == config.MATERIAL_GAP:
        return [
            ValidationIssue(
                WARNING,
                "requested.gap",
                f"The {analysis.requested_basis} of "
                f"{fmt.fmt_money(analysis.requested_valuation)} sits "
                f"{fmt.fmt_pct(analysis.requested_gap_percent or 0)} above the base estimate. "
                "Section 7 lists an unusually high ask as a discount factor; it is reported "
                "here rather than applied, so the gap stays an independent measurement.",
            )
        ]
    return []


def _consent(profile: CompanyProfile) -> list[ValidationIssue]:
    """Consent must be explicit. An inferred timestamp is a defect, not a default."""
    consent = profile.user.consent
    granted = any(
        (consent.analysis_consent, consent.data_storage_consent, consent.contact_consent)
    )
    if consent.timestamp and not granted:
        return [
            ValidationIssue(
                ERROR,
                "consent.inferred",
                "A consent timestamp is present although no consent flag was set.",
            )
        ]
    return []


def _contradictions(profile: CompanyProfile) -> list[ValidationIssue]:
    """Carry every contradiction found upstream into the findings, unresolved."""
    return [
        ValidationIssue(WARNING, "deck.contradiction", text)
        for text in profile.contradictions
    ]
