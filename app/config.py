"""Configuration: environment, firm identity, valuation defaults and visual style.

Everything a run can be tuned by lives here. The analytical modules import
constants from this file rather than reading the environment themselves, so one
import is enough to see what a run was configured with.

The valuation defaults in this file are *stated priors*, not market data. Every
one of them enters the ledger as an ANALYST ASSUMPTION and is printed in the
assumptions list, because section 17 of the standard forbids silently
substituting an industry average for a company fact.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Environment ------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env here rather than in main.py: the path is anchored to the project
# root, so `python path\to\app\main.py` started from anywhere still finds the
# key, and it runs before the os.getenv calls below.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ModuleNotFoundError:  # pragma: no cover - optional convenience
    pass

# --- Firm identity ----------------------------------------------------------

FIRM_NAME = "TEN CAPITAL GROUP"
FIRM_SHORT = "TEN Capital"
DOC_TITLE = "STARTUP VALUATION ANALYSIS"
CONFIDENTIALITY = "Confidential – for recipients only."

DISCLAIMER = (
    "This analysis is based solely on information supplied by the company, user, and stated "
    "comparable financing data. Startup valuation is inherently subjective and actual financing "
    "terms depend on investor demand, market conditions, security structure, negotiation, and "
    "due diligence."
)

# --- Model ------------------------------------------------------------------

LLM_MODEL = os.getenv("VALUATION_MODEL", "claude-opus-5")
LLM_EFFORT = os.getenv("VALUATION_EFFORT", "high")

# --- Paths ------------------------------------------------------------------

ASSETS_DIR = PROJECT_ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
OUTPUT_DIR = PROJECT_ROOT / "output"
FIRM_LOGO = ASSETS_DIR / "TEN_Capital_logo_footer.png"

# --- Source precedence ------------------------------------------------------
#
# Section 2 of the standard, highest authority first. `SOURCE_RANK` decides which
# of two competing values for the same metric wins, and the winner's type is what
# the report prints beside the figure.

USER = "USER"
DECK = "DECK"
COMPARABLE = "COMPARABLE"
CALCULATED = "CALCULATED"
ASSUMPTION = "ASSUMPTION"
SOURCE_TYPES = (USER, DECK, COMPARABLE, CALCULATED, ASSUMPTION)
SOURCE_RANK = {USER: 0, DECK: 1, COMPARABLE: 2, CALCULATED: 3, ASSUMPTION: 4}

SOURCE_LABELS = {
    USER: "User supplied",
    DECK: "Pitch deck",
    COMPARABLE: "Comparable financing data",
    CALCULATED: "Calculated",
    ASSUMPTION: "ANALYST ASSUMPTION",
}

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"
CONFIDENCE_LEVELS = (HIGH, MEDIUM, LOW)

NOT_DISCLOSED = "Not disclosed"
INSUFFICIENT = "Insufficient information"
NO_COMPARABLES = "Comparable financing data not supplied."

# --- Stages -----------------------------------------------------------------

PRE_REVENUE = "PRE_REVENUE"
SEED = "SEED"
SERIES_A = "SERIES_A"
GROWTH = "GROWTH"
STAGES = (PRE_REVENUE, SEED, SERIES_A, GROWTH)

STAGE_LABELS = {
    PRE_REVENUE: "Pre-revenue / very early stage",
    SEED: "Early revenue / seed",
    SERIES_A: "Series A",
    GROWTH: "Growth stage",
}

# Revenue thresholds that separate the stage bands when the deck's own stage
# label is absent or contradicts the numbers. Revenue, not vocabulary, decides
# which methods are allowed to run.
SEED_REVENUE_FLOOR = 250_000.0
SERIES_A_REVENUE_FLOOR = 2_000_000.0
GROWTH_REVENUE_FLOOR = 10_000_000.0

# Below this, revenue is too thin to carry a multiple at all and the company is
# treated as pre-revenue for method selection.
MEANINGFUL_REVENUE_FLOOR = 250_000.0

# --- Method weights ---------------------------------------------------------
#
# Section 4 assigns emphasis by stage. These are the starting weights; the engine
# renormalises across the methods that actually ran, then shifts weight toward
# comparable financings when real comparables were supplied.

METHOD_WEIGHTS: dict[str, dict[str, float]] = {
    PRE_REVENUE: {
        "comparable_financing": 0.40,
        "scorecard": 0.35,
        "venture_method": 0.25,
    },
    SEED: {
        "comparable_financing": 0.35,
        "revenue_multiple": 0.30,
        "scorecard": 0.15,
        "venture_method": 0.20,
    },
    SERIES_A: {
        "revenue_multiple": 0.45,
        "comparable_financing": 0.35,
        "venture_method": 0.20,
    },
    GROWTH: {
        "revenue_multiple": 0.55,
        "comparable_financing": 0.30,
        "venture_method": 0.15,
    },
}

# Weight added to comparable financings when at least MIN_COMPARABLES usable
# transactions were supplied, taken pro rata from the other methods. Evidence
# from named transactions should outrank a stated prior.
COMPARABLE_WEIGHT_BONUS = 0.10
MIN_COMPARABLES = 3

# --- Valuation priors -------------------------------------------------------
#
# ANALYST ASSUMPTIONS, used only when supplied comparables cannot produce a
# baseline. Each is (low, base, high).

# Revenue / ARR multiples by stage, applied to annualised revenue.
STAGE_REVENUE_MULTIPLES: dict[str, tuple[float, float, float]] = {
    SEED: (4.0, 6.5, 10.0),
    SERIES_A: (4.0, 6.0, 9.0),
    GROWTH: (3.0, 5.0, 7.5),
}

# Pre-money baselines for a company with no revenue to multiply, in dollars.
STAGE_PREMONEY_BASELINE: dict[str, tuple[float, float, float]] = {
    PRE_REVENUE: (3_000_000.0, 6_000_000.0, 10_000_000.0),
    SEED: (6_000_000.0, 10_000_000.0, 16_000_000.0),
    SERIES_A: (15_000_000.0, 25_000_000.0, 40_000_000.0),
    GROWTH: (40_000_000.0, 70_000_000.0, 120_000_000.0),
}

# Growth, margin and retention are continuous drivers, so they are sized by a
# formula rather than a step: every point above the reference moves the valuation
# by `sensitivity`, within `cap`. A company at the reference gets no adjustment.
#
# These sit in the premium/discount framework (section 7), NOT inside the revenue
# multiple. Multiplying the multiple by (1 + x) and multiplying the valuation by
# (1 + x) are the same arithmetic, and doing it in one place is what keeps the
# section 8 bridge free of double counting.
GROWTH_REFERENCE = 0.50
GROWTH_SENSITIVITY = 0.35
GROWTH_CAP = 0.30

MARGIN_REFERENCE = 0.70
MARGIN_SENSITIVITY = 0.50
MARGIN_CAP = 0.12

NRR_REFERENCE = 1.00
NRR_SENSITIVITY = 0.50
NRR_CAP = 0.15

# Revenue from the single largest customer, and the discount it carries.
# Checked most concentrated first.
CONCENTRATION_BANDS = ((0.40, 0.15), (0.25, 0.10), (0.15, 0.05))

# Below this share, concentration is a positive rather than a neutral.
CONCENTRATION_PREMIUM_CEILING = 0.10

# --- Venture capital method -------------------------------------------------

VC_YEARS_TO_EXIT = {PRE_REVENUE: 7, SEED: 6, SERIES_A: 5, GROWTH: 4}

# Target gross multiple on invested capital, not an IRR. Earlier money carries
# more mortality risk and so demands more of the survivors.
VC_TARGET_RETURN = {PRE_REVENUE: 20.0, SEED: 12.0, SERIES_A: 8.0, GROWTH: 4.0}

# Dilution the engine assumes between this round and exit, which the VC method
# has to reverse out before it can quote a present pre-money. A seed company has
# more rounds left to raise than a growth company, so it gives up more.
VC_FUTURE_DILUTION = {PRE_REVENUE: 0.60, SEED: 0.50, SERIES_A: 0.35, GROWTH: 0.20}

# Exit revenue multiple band applied to the modelled exit-year revenue.
VC_EXIT_MULTIPLE = (3.0, 5.0, 8.0)

# Annual growth assumed to the exit year when the company has revenue and the
# deck gives no plan, and the decay applied to a stated growth rate each year.
VC_DEFAULT_GROWTH = 0.60
VC_GROWTH_DECAY = 0.80

# --- Scorecard --------------------------------------------------------------
#
# Factor weights for the pre-revenue scorecard. Each factor is scored against a
# neutral 1.00 baseline; the weighted product scales the stage baseline. Weights
# sum to 1.00.

SCORECARD_WEIGHTS: dict[str, float] = {
    "team": 0.28,
    "opportunity": 0.22,
    "product": 0.16,
    "defensibility": 0.14,
    "traction": 0.12,
    "partnerships": 0.08,
}

# A factor may not move the baseline by more than this, in either direction.
SCORECARD_FACTOR_BAND = (0.60, 1.60)
SCORECARD_TOTAL_BAND = (0.55, 1.75)

# --- Premium / discount framework -------------------------------------------
#
# Nominal adjustment weights, applied additively to the weighted method baseline
# (section 8: baseline x (1 + sum of factors)). Nothing is applied without an
# evidenced trigger.

ADJUSTMENT_STEP = {"strong": 0.15, "moderate": 0.10, "mild": 0.05}

# The net of all factors is clamped into this band: past it, the adjustment is
# doing the work the methods should be doing.
NET_ADJUSTMENT_BAND = (-0.40, 0.40)

# The venture method stacks four assumptions — exit multiple, required return,
# dilution to exit and a growth path — so it is the most fragile method in the
# set. It keeps its nominal stage weight and never inherits weight freed by a
# method that excluded itself; the evidence-led methods absorb that instead.
CAPPED_METHODS = ("venture_method",)

# Factors printed on the page, per column. Everything computed still reaches the
# JSON record.
MAX_PRINTED_FACTORS = 4

# --- Requested-valuation classification -------------------------------------

BELOW_RANGE = "BELOW MARKET RANGE"
WITHIN_RANGE = "WITHIN DEFENSIBLE RANGE"
ABOVE_RANGE = "ABOVE DEFENSIBLE RANGE"
MATERIAL_GAP = "MATERIAL VALUATION GAP"

# Premium to the base case past which "above the range" becomes "a material gap".
MATERIAL_GAP_THRESHOLD = 0.50

# --- Range shaping ----------------------------------------------------------

# A range narrower than this reads as false precision, so the engine widens it
# symmetrically around the base and records that it did.
MIN_RANGE_SPREAD = 0.20

# --- Visual style -----------------------------------------------------------

NAVY = "#0A2342"
INK = "#1B1F27"
MUTED = "#5A6472"
GHOST = "#8A8F98"
RULE = "#C9D2DE"
BAND = "#F2F4F7"
ACCENT = "#1F6FB2"
POSITIVE = "#1D6B4F"
WARN = "#B26B1F"
FLAG = "#A5402A"
PAPER = "#FFFFFF"

# The status chip in section 2, coloured by how far the ask sits from the range.
STATUS_COLORS = {
    BELOW_RANGE: ACCENT,
    WITHIN_RANGE: POSITIVE,
    ABOVE_RANGE: WARN,
    MATERIAL_GAP: FLAG,
    INSUFFICIENT: MUTED,
}

# Open Sans is the house face. It is used when the TTFs are present in
# assets/fonts (see README); otherwise the report falls back to the ReportLab
# core fonts, which need no files and render identically on any host.
FONT_FAMILY = "OpenSans"
FALLBACK_FONTS = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")

# Geometry, in inches. Portrait letter: the page is one column of six sections,
# with the wider sections splitting internally into two.
PAGE_MARGIN = 0.45
HEADER_HEIGHT = 0.80
FOOTER_BAND_HEIGHT = 0.80
BAND_GAP = 0.12
