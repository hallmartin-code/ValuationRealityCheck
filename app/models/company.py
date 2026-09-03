"""The company side of the analysis: what is known, and where each fact came from.

Two ideas carry the file.

`Sourced` — a value that knows where it came from, classified USER / DECK /
COMPARABLE / CALCULATED / ASSUMPTION, so nothing reaches the PDF without
provenance and section 2's precedence order can be enforced mechanically.

`CompanyProfile` — the normalised, deduplicated view of the company that the
valuation engine reads. Nothing downstream of this file touches deck text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime
from typing import Any

from .. import config
from ..utils import normalization as norm


# --- Provenance -------------------------------------------------------------


@dataclass
class Sourced:
    """One value plus the story of where it came from.

    `source` is the citation: "slide 8" for DECK, the supplying field for USER,
    the formula for CALCULATED, the reasoning for an ASSUMPTION.
    """

    metric: str
    value: Any
    source_type: str
    source: str = ""
    confidence: str = config.MEDIUM
    unit: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in config.SOURCE_TYPES:
            raise ValueError(
                f"{self.metric}: source_type {self.source_type!r} "
                f"must be one of {config.SOURCE_TYPES}"
            )
        if self.confidence not in config.CONFIDENCE_LEVELS:
            raise ValueError(f"{self.metric}: confidence {self.confidence!r} is not a level")

    @property
    def rank(self) -> int:
        return config.SOURCE_RANK[self.source_type]

    @property
    def is_assumption(self) -> bool:
        return self.source_type == config.ASSUMPTION

    def citation(self) -> str:
        """The provenance line printed beside the figure."""
        label = config.SOURCE_LABELS[self.source_type]
        return f"{label}, {self.source}" if self.source else label

    def number(self) -> float | None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            return None
        return float(self.value)


@dataclass
class Ledger:
    """The ordered record of every material input, and the precedence referee.

    `record` will not let a lower-authority source overwrite a higher-authority
    one. That is section 2 implemented once, rather than re-argued at every call
    site: an assumption offered for a metric the deck already stated is dropped,
    and the drop is remembered in `superseded` so the audit trail shows the
    engine considered it.
    """

    entries: list[Sourced] = field(default_factory=list)
    superseded: list[Sourced] = field(default_factory=list)

    def record(
        self,
        metric: str,
        value: Any,
        source_type: str,
        source: str = "",
        confidence: str = config.MEDIUM,
        unit: str = "",
        notes: str = "",
    ) -> Sourced:
        """Add an entry and return whichever entry now holds the metric."""
        return self.add(Sourced(metric, value, source_type, source, confidence, unit, notes))

    def add(self, entry: Sourced) -> Sourced:
        existing = self.get(entry.metric)
        if existing is not None:
            if existing.rank <= entry.rank:
                self.superseded.append(entry)
                return existing
            self.entries.remove(existing)
            self.superseded.append(existing)
        self.entries.append(entry)
        return entry

    def get(self, metric: str) -> Sourced | None:
        return next((entry for entry in self.entries if entry.metric == metric), None)

    def value(self, metric: str, default: Any = None) -> Any:
        entry = self.get(metric)
        return default if entry is None else entry.value

    def number(self, metric: str, default: float | None = None) -> float | None:
        entry = self.get(metric)
        if entry is None:
            return default
        found = entry.number()
        return default if found is None else found

    def citation(self, metric: str, default: str = config.NOT_DISCLOSED) -> str:
        entry = self.get(metric)
        return default if entry is None else entry.citation()

    def by_type(self, source_type: str) -> list[Sourced]:
        return [entry for entry in self.entries if entry.source_type == source_type]

    def assumptions(self) -> list[Sourced]:
        return self.by_type(config.ASSUMPTION)

    def __len__(self) -> int:
        return len(self.entries)


# --- Deck extraction --------------------------------------------------------


@dataclass
class DeckValue:
    """A single field as the extractor found it, quoted rather than interpreted."""

    text: str = ""
    slide: int | None = None
    quote: str = ""

    @property
    def present(self) -> bool:
        return bool(self.text.strip())

    def citation(self) -> str:
        return f"slide {self.slide}" if self.slide else "deck"


@dataclass
class DeckFacts:
    """Everything the extractor found, grouped the way section 1 groups its inputs."""

    company: dict[str, DeckValue] = field(default_factory=dict)
    financing: dict[str, DeckValue] = field(default_factory=dict)
    metrics: dict[str, DeckValue] = field(default_factory=dict)
    market: dict[str, DeckValue] = field(default_factory=dict)
    quality: dict[str, DeckValue] = field(default_factory=dict)
    slide_count: int = 0
    source_file: str = ""
    contradictions: list[str] = field(default_factory=list)

    def get(self, group: str, key: str) -> DeckValue:
        return getattr(self, group, {}).get(key, DeckValue())

    def text(self, group: str, key: str) -> str:
        return self.get(group, key).text

    def present(self, group: str, key: str) -> bool:
        return self.get(group, key).present


# --- Normalised company view ------------------------------------------------


@dataclass
class Identity:
    """Who the company is. Every field is a string; absence is an empty string."""

    name: str = ""
    website: str = ""
    headquarters: str = ""
    geography: str = ""
    sector: str = ""
    subsector: str = ""
    business_model: str = ""
    stage_text: str = ""

    def header_line(self) -> str:
        from ..utils.formatting import join_facts

        return join_facts(self.sector or self.subsector, self.stage_text, self.geography)


@dataclass
class RoundTerms:
    """The financing being proposed, normalised to pre / post / raise."""

    raise_amount: float | None = None
    instrument: str = ""
    requested_pre_money: float | None = None
    requested_post_money: float | None = None
    valuation_cap: float | None = None
    discount_rate: float | None = None
    committed: float | None = None
    prior_raised: float | None = None

    @property
    def is_convertible(self) -> bool:
        """Whether the instrument converts rather than pricing the round today."""
        lowered = self.instrument.lower()
        return any(word in lowered for word in ("safe", "note", "convertible", "kiss"))

    @property
    def reference_valuation(self) -> float | None:
        """The valuation management is effectively asking for.

        A priced round states a pre-money. A SAFE or note states a cap instead,
        and the cap is what an investor is underwriting, so it stands in — the
        report says which of the two it used rather than blending them.
        """
        if self.requested_pre_money is not None:
            return self.requested_pre_money
        if self.is_convertible and self.valuation_cap is not None:
            return self.valuation_cap
        return self.valuation_cap

    @property
    def reference_basis(self) -> str:
        if self.requested_pre_money is not None:
            return "requested pre-money"
        if self.valuation_cap is not None:
            return "valuation cap"
        return ""


@dataclass
class OperatingMetrics:
    """The operating picture, in consistent units: dollars, fractions, months."""

    revenue: float | None = None
    revenue_basis: str = norm.CURRENT
    arr: float | None = None
    arr_basis: str = ""
    mrr: float | None = None
    growth_rate: float | None = None
    gross_margin: float | None = None
    ebitda: float | None = None
    burn: float | None = None
    runway_months: float | None = None
    customer_count: float | None = None
    acv: float | None = None
    largest_customer_pct: float | None = None
    retention: float | None = None
    gross_retention: float | None = None
    nrr: float | None = None
    churn: float | None = None
    cac: float | None = None
    ltv: float | None = None
    ltv_cac: float | None = None
    sales_cycle_months: float | None = None
    pipeline: float | None = None
    customer_growth: float | None = None
    tam: float | None = None
    sam: float | None = None
    som: float | None = None

    @property
    def countable_revenue(self) -> float | None:
        """Revenue a multiple may be applied to, or None.

        ARR leads where it exists, because a recurring figure is what the
        multiple bands are quoted against. Revenue on a projected, pipeline or
        aspirational basis is excluded entirely — section 3.
        """
        if self.arr is not None:
            return self.arr
        if self.revenue is not None and norm.is_countable(self.revenue_basis):
            return self.revenue
        return None

    @property
    def revenue_label(self) -> str:
        return "ARR" if self.arr is not None else "Revenue"

    @property
    def has_meaningful_revenue(self) -> bool:
        revenue = self.countable_revenue
        return revenue is not None and revenue >= config.MEANINGFUL_REVENUE_FLOOR


@dataclass
class QualityFactors:
    """The company-quality inputs behind the scorecard and the adjustments.

    Booleans are three-valued on purpose: None means the deck never addressed the
    point, and section 17 forbids reading silence as a No.
    """

    ip_summary: str = ""
    patents_filed: float | None = None
    patents_granted: float | None = None
    proprietary_technology: str = ""
    proprietary_data: str = ""
    regulatory_approvals: str = ""
    regulatory_status: str = ""
    product_maturity: str = ""
    differentiation: str = ""
    founder_experience: str = ""
    management_quality: str = ""
    relevant_exits: str = ""
    domain_expertise: str = ""
    customer_validation: str = ""
    strategic_partnerships: str = ""

    # The classifier's reading of the qualitative fields, on a -2..+2 scale where
    # 0 is "as expected for the stage". Set by the extraction pass, never guessed
    # at by the valuation engine. `evidence` separates "the deck addressed this
    # and it is par" from "the deck never addressed it", which score alone cannot.
    scores: dict[str, int] = field(default_factory=dict)
    evidence: dict[str, bool] = field(default_factory=dict)
    score_rationales: dict[str, str] = field(default_factory=dict)

    def score(self, factor: str) -> int | None:
        value = self.scores.get(factor)
        return None if value is None else int(norm.clamp(float(value), -2, 2))

    def evidenced(self, factor: str) -> bool:
        return bool(self.evidence.get(factor))


@dataclass
class Comparable:
    """One externally supplied comparable financing.

    Never fabricated: instances exist only because a user or an upstream
    component supplied them. `usable` is what the comparable method filters on —
    a row without a pre-money valuation cannot anchor anything.
    """

    company: str = ""
    sector: str = ""
    stage: str = ""
    financing_date: str = ""
    round_size: float | None = None
    pre_money: float | None = None
    post_money: float | None = None
    revenue: float | None = None
    arr: float | None = None
    growth: float | None = None
    geography: str = ""
    lead_investor: str = ""
    source: str = ""
    source_date: str = ""

    def __post_init__(self) -> None:
        # A row may arrive with only two of pre / post / round size filled in.
        self.pre_money, self.post_money, self.round_size = norm.complete_round(
            self.pre_money, self.post_money, self.round_size
        )

    @property
    def usable(self) -> bool:
        return self.pre_money is not None and self.pre_money > 0

    @property
    def recurring_revenue(self) -> float | None:
        return self.arr if self.arr is not None else self.revenue

    @property
    def implied_multiple(self) -> float | None:
        """Pre-money over revenue at financing, when both are known."""
        revenue = self.recurring_revenue
        if not self.usable or revenue is None or revenue <= 0:
            return None
        return norm.safe_div(self.pre_money, revenue)

    def label(self) -> str:
        from ..utils.formatting import join_facts

        return join_facts(self.company, self.stage, self.financing_date, separator=", ")


@dataclass
class Consent:
    """Section 16: consent is never inferred. Every flag defaults to False."""

    analysis_consent: bool = False
    data_storage_consent: bool = False
    contact_consent: bool = False
    timestamp: str | None = None

    def stamp(self) -> None:
        """Record when consent was given — only if some consent actually was."""
        if any((self.analysis_consent, self.data_storage_consent, self.contact_consent)):
            self.timestamp = datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class UserContext:
    name: str = ""
    email: str = ""
    consent: Consent = field(default_factory=Consent)


@dataclass
class CompanyProfile:
    """The complete, normalised input to the valuation engine.

    Built either by the extraction pipeline or, in tests and in programmatic use,
    directly. The engine reads nothing else, which is what makes a run
    reproducible from the JSON record alone.
    """

    identity: Identity = field(default_factory=Identity)
    round: RoundTerms = field(default_factory=RoundTerms)
    metrics: OperatingMetrics = field(default_factory=OperatingMetrics)
    quality: QualityFactors = field(default_factory=QualityFactors)
    comparables: list[Comparable] = field(default_factory=list)
    user: UserContext = field(default_factory=UserContext)
    ledger: Ledger = field(default_factory=Ledger)
    missing_information: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    deck_source: str = ""
    slide_count: int = 0
    analysis_date: date = field(default_factory=date.today)

    @property
    def stage(self) -> str:
        """The stage band that governs method selection."""
        return norm.infer_stage(
            self.identity.stage_text,
            self.metrics.countable_revenue,
            self.round.raise_amount,
        )

    @property
    def usable_comparables(self) -> list[Comparable]:
        return [item for item in self.comparables if item.usable]

    def note_missing(self, label: str) -> None:
        if label not in self.missing_information:
            self.missing_information.append(label)

    def note_contradiction(self, text: str) -> None:
        if text not in self.contradictions:
            self.contradictions.append(text)


# --- Serialisation ----------------------------------------------------------


def to_plain(value: Any) -> Any:
    """Recursively convert dataclasses, dates and tuples into JSON-safe values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def field_names(dataclass_type: type) -> tuple[str, ...]:
    """The declared field names of a dataclass, used to validate user input keys."""
    return tuple(item.name for item in fields(dataclass_type))
