"""The valuation side of the analysis: methods, adjustments, bridge, dilution.

Every dataclass here is an output — filled in by the valuation package, read by
the reporting and output packages. Keeping the results in typed objects rather
than dictionaries is what lets the validator re-derive the arithmetic and the
JSON record stay a faithful copy of what the page printed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..utils import normalization as norm

PREMIUM = "PREMIUM"
DISCOUNT = "DISCOUNT"


@dataclass
class ValuationRange:
    """A low / base / high pre-money range."""

    low: float
    base: float
    high: float

    def scaled(self, factor: float) -> ValuationRange:
        return ValuationRange(self.low * factor, self.base * factor, self.high * factor)

    def ordered(self) -> ValuationRange:
        """Return the same three numbers sorted, so low <= base <= high always holds.

        A method can legitimately produce a base outside its own low/high — a
        growth-adjusted multiple pushing past the 75th percentile of the comp set,
        say — and an out-of-order range would render as a broken band on the page.
        """
        low, base, high = sorted((self.low, self.base, self.high))
        return ValuationRange(low, base, high)

    def widened(self, minimum_spread: float = config.MIN_RANGE_SPREAD) -> ValuationRange:
        """Widen a range that has collapsed onto its base.

        A low and high within a few percent of the base is false precision: it
        claims the evidence pins the valuation more tightly than it does.
        """
        if self.base <= 0:
            return self
        spread = (self.high - self.low) / self.base
        if spread >= minimum_spread:
            return self
        half = minimum_spread / 2
        return ValuationRange(self.base * (1 - half), self.base, self.base * (1 + half))

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.low, self.base, self.high)


@dataclass
class MethodResult:
    """One valuation method's answer, plus everything needed to reproduce it.

    `included` is False for a method that was considered and could not run; the
    page prints only the methods that ran, and the JSON record keeps the rest
    with the reason they were excluded.
    """

    name: str
    included: bool = False
    low: float | None = None
    base: float | None = None
    high: float | None = None
    weight: float = 0.0
    basis: str = ""
    detail: str = ""
    exclusion_reason: str = ""
    # A method that has already priced company quality into its answer, so the
    # premium/discount factors must not be applied to it a second time.
    quality_adjusted: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def range(self) -> ValuationRange | None:
        if not self.included or self.base is None:
            return None
        low = self.low if self.low is not None else self.base
        high = self.high if self.high is not None else self.base
        return ValuationRange(low, self.base, high).ordered()


@dataclass
class Adjustment:
    """One premium or discount factor, with the evidence that triggered it."""

    label: str
    direction: str
    pct: float
    rationale: str
    metric: str = ""
    source: str = config.DECK

    @property
    def signed_pct(self) -> float:
        return self.pct if self.direction == PREMIUM else -self.pct


@dataclass
class BridgeLine:
    """One line of the valuation bridge, in the order it is printed."""

    label: str
    pct: float | None
    value: float
    is_total: bool = False


@dataclass
class DilutionCase:
    """The financing consequence of one valuation case."""

    label: str
    pre_money: float | None
    post_money: float | None
    new_investor_pct: float | None
    note: str = ""


@dataclass
class SensitivityTable:
    """A compact two-dimensional sensitivity grid.

    `rows` are (row label, [cell strings]); the cells are pre-formatted so the
    renderer never has to know what the axes mean.
    """

    title: str = ""
    row_axis: str = ""
    column_axis: str = ""
    column_labels: list[str] = field(default_factory=list)
    rows: list[tuple[str, list[str]]] = field(default_factory=list)
    note: str = ""
    base_cell: tuple[int, int] | None = None

    @property
    def present(self) -> bool:
        return bool(self.rows and self.column_labels)


@dataclass
class ValidationIssue:
    """One finding from the pre-render validation pass."""

    severity: str
    rule: str
    message: str


@dataclass
class ValuationAnalysis:
    """The complete output of the valuation engine."""

    stage: str = config.PRE_REVENUE
    range: ValuationRange = field(default_factory=lambda: ValuationRange(0.0, 0.0, 0.0))
    baseline: ValuationRange | None = None
    methods: list[MethodResult] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    net_adjustment: float = 0.0
    adjustment_applied_share: float = 1.0
    bridge: list[BridgeLine] = field(default_factory=list)
    dilution: list[DilutionCase] = field(default_factory=list)
    sensitivity: SensitivityTable = field(default_factory=SensitivityTable)

    requested_valuation: float | None = None
    requested_basis: str = ""
    requested_gap_amount: float | None = None
    requested_gap_percent: float | None = None
    requested_status: str = ""
    requested_statement: str = ""

    confidence: str = config.LOW
    confidence_reason: str = ""
    decision_factors: list[str] = field(default_factory=list)
    key_milestone: str = ""

    assumptions: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    comparable_note: str = config.NO_COMPARABLES

    @property
    def used_methods(self) -> list[MethodResult]:
        return [method for method in self.methods if method.included]

    @property
    def excluded_methods(self) -> list[MethodResult]:
        return [method for method in self.methods if not method.included]

    def method(self, name: str) -> MethodResult | None:
        return next((item for item in self.methods if item.name == name), None)

    def premiums(self) -> list[Adjustment]:
        return [item for item in self.adjustments if item.direction == PREMIUM]

    def discounts(self) -> list[Adjustment]:
        return [item for item in self.adjustments if item.direction == DISCOUNT]

    def dilution_case(self, label: str) -> DilutionCase | None:
        return next((item for item in self.dilution if item.label == label), None)

    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "ERROR"]

    def post_money(self, pre_money: float | None, raise_amount: float | None) -> float | None:
        if pre_money is None or raise_amount is None:
            return None
        return pre_money + raise_amount

    def ownership(self, pre_money: float | None, raise_amount: float | None) -> float | None:
        post = self.post_money(pre_money, raise_amount)
        return norm.safe_div(raise_amount, post)
