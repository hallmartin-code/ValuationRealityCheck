"""ReportLab renderer: the one-page TEN Capital startup valuation analysis.

The page is portrait letter with a fixed structure — a navy header band, six
stacked sections, and a footer band carrying the confidence line, the analyst
flags and the disclaimer. Section 2 is the visual focal point: an investor
should be able to read the range, the ask and the verdict without reading a word
of the rest.

One page is a hard constraint. The build is retried at progressively tighter type
profiles until it fits, and the last profiles also trim the narrative sections to
a word budget — content is reduced before type drops below a readable size
(section 15).
"""

from __future__ import annotations

import io
from datetime import date
from html import escape
from pathlib import Path
from typing import Any, Iterable

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .. import config
from ..models.company import CompanyProfile
from ..models.valuation import ValuationAnalysis
from ..utils import formatting as fmt
from ..valuation import dilution as dilution_model
from ..valuation import comparable_financing

PAGE_WIDTH, PAGE_HEIGHT = letter

# (body, leading, heading, table, section gap, narrative word budget)
#
# The build takes the first profile that fits on one page, so the report is set
# as large as it can be rather than as small as it must be.
_PROFILES: tuple[tuple[float, float, float, float, float, int], ...] = (
    (9.5, 12.0, 10.8, 8.8, 10.0, 60),
    (9.2, 11.5, 10.4, 8.5, 9.0, 56),
    (8.9, 11.0, 10.0, 8.2, 8.0, 52),
    (8.6, 10.6, 9.6, 7.9, 7.0, 48),
    (8.3, 10.2, 9.2, 7.6, 6.0, 42),
    (8.0, 9.8, 8.8, 7.3, 5.0, 36),
    (7.6, 9.3, 8.4, 7.0, 4.2, 30),
    (7.2, 8.8, 8.0, 6.7, 3.5, 26),
)

_MIN_FOOTER = 0.74 * inch
_MAX_FOOTER = 1.45 * inch

# Section 2's band is drawn, not typeset, so it keeps a fixed height across
# profiles — shrinking the focal point to win space defeats the point of it.
_BAND_HEIGHT = 1.08 * inch


def render(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    out_path: str | Path,
    report_date: date | None = None,
) -> dict[str, Any]:
    """Render the analysis to a single-page PDF. Returns {"path", "pages", "profile"}."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = report_date or profile.analysis_date

    pdf_bytes, pages, index = b"", 0, 0
    for index, type_profile in enumerate(_PROFILES):
        pdf_bytes, pages = _build(profile, analysis, type_profile, stamp)
        if pages <= 1:
            break

    out_path.write_bytes(pdf_bytes)
    return {"path": out_path, "pages": pages, "profile": index}


# --- Fonts ------------------------------------------------------------------


def _fonts() -> tuple[str, str, str]:
    """Register Open Sans if its TTFs are present, else use the core fonts.

    Open Sans is the house face, but the report has to render identically on a
    host that has no font files at all — so the family is used when it is
    available and Helvetica stands in when it is not. Nothing about the layout
    depends on which one is in use.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    family = config.FONT_FAMILY
    registered = pdfmetrics.getRegisteredFontNames()
    if family in registered:
        return family, f"{family}-Bold", f"{family}-Italic"

    faces = {
        family: ("OpenSans-Regular.ttf", "OpenSans.ttf"),
        f"{family}-Bold": ("OpenSans-Bold.ttf",),
        f"{family}-Italic": ("OpenSans-Italic.ttf",),
    }
    resolved: dict[str, Path] = {}
    for name, candidates in faces.items():
        for candidate in candidates:
            path = config.FONTS_DIR / candidate
            if path.exists():
                resolved[name] = path
                break
    if len(resolved) < len(faces):
        return config.FALLBACK_FONTS

    try:
        for name, path in resolved.items():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    except Exception:
        return config.FALLBACK_FONTS
    return family, f"{family}-Bold", f"{family}-Italic"


BASE_FONT, BOLD_FONT, ITALIC_FONT = _fonts()


# --- Geometry ---------------------------------------------------------------


def _content_width() -> float:
    return PAGE_WIDTH - 2 * config.PAGE_MARGIN * inch


def _body_bounds(footer_height: float) -> tuple[float, float]:
    """Return (bottom y, height) of the body frame."""
    margin = config.PAGE_MARGIN * inch
    top = PAGE_HEIGHT - margin - config.HEADER_HEIGHT * inch - config.BAND_GAP * inch
    bottom = margin + footer_height + config.BAND_GAP * inch
    return bottom, max(top - bottom, 60.0)


def _measure(flowables: Iterable[Any], width: float) -> float:
    total = 0.0
    for flowable in flowables:
        _, height = flowable.wrap(width, 10_000)
        total += height
        total += float(getattr(flowable, "getSpaceBefore", lambda: 0)() or 0)
        total += float(getattr(flowable, "getSpaceAfter", lambda: 0)() or 0)
    return total


# --- Build ------------------------------------------------------------------


def _build(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    type_profile: tuple[float, float, float, float, float, int],
    stamp: date,
) -> tuple[bytes, int]:
    """Build the document once at a given type profile; return (bytes, page count)."""
    styles = _styles(type_profile)
    width = _content_width()
    footer_height = _footer_height(profile, analysis, styles, width)

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=config.PAGE_MARGIN * inch,
        rightMargin=config.PAGE_MARGIN * inch,
        topMargin=config.PAGE_MARGIN * inch,
        bottomMargin=config.PAGE_MARGIN * inch,
        title=f"{profile.identity.name} — Startup Valuation Analysis",
        author=config.FIRM_SHORT,
        subject="Startup valuation analysis",
    )
    bottom, available = _body_bounds(footer_height)
    doc.addPageTemplates(
        [
            PageTemplate(
                id="valuation",
                frames=[
                    Frame(
                        config.PAGE_MARGIN * inch,
                        bottom,
                        width,
                        available,
                        id="body",
                        leftPadding=0,
                        rightPadding=0,
                        topPadding=0,
                        bottomPadding=0,
                    )
                ],
                onPage=_page_furniture(profile, analysis, styles, stamp, footer_height),
            )
        ]
    )

    sections = [
        _snapshot_section(profile, analysis, styles, width),
        _range_section(profile, analysis, styles, width),
        _methods_section(analysis, styles, width),
        _drivers_section(analysis, styles, width),
        _round_section(profile, analysis, styles, width),
        _closing_section(profile, analysis, styles, width, type_profile[5]),
    ]
    story = _assemble(sections, available, width, type_profile[4])
    doc.build(story)
    return buffer.getvalue(), doc.page


def _assemble(
    sections: list[list[Any]], available: float, width: float, gap: float
) -> list[Any]:
    """Join the sections, distributing leftover height evenly between them."""
    flowables: list[Any] = [item for section in sections for item in section]
    slots = len(sections) - 1
    if slots <= 0:
        return flowables

    leftover = available - _measure(flowables, width) - gap * slots
    extra = max(0.0, min(leftover / slots, gap))

    spaced: list[Any] = []
    for index, section in enumerate(sections):
        if index:
            spaced.append(Spacer(1, gap + extra))
        spaced += section
    return spaced


# --- Styles -----------------------------------------------------------------


def _styles(type_profile: tuple[float, float, float, float, float, int]) -> dict[str, Any]:
    body, leading, heading, table, _, _ = type_profile
    return {
        "sizes": {"body": body, "table": table, "heading": heading, "leading": leading},
        "heading": ParagraphStyle(
            "heading",
            fontName=BOLD_FONT,
            fontSize=heading,
            leading=heading + 1.4,
            textColor=HexColor(config.NAVY),
            spaceBefore=0,
            spaceAfter=1.5,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=BASE_FONT,
            fontSize=body,
            leading=leading,
            textColor=HexColor(config.INK),
            spaceAfter=2.0,
        ),
        "small": ParagraphStyle(
            "small",
            fontName=BASE_FONT,
            fontSize=body - 0.7,
            leading=leading - 1.4,
            textColor=HexColor(config.MUTED),
            spaceAfter=1.5,
        ),
        "note": ParagraphStyle(
            "note",
            fontName=ITALIC_FONT,
            fontSize=body - 0.9,
            leading=leading - 1.6,
            textColor=HexColor(config.MUTED),
            spaceBefore=1.0,
            spaceAfter=1.0,
        ),
        "driver": ParagraphStyle(
            "driver",
            fontName=BASE_FONT,
            fontSize=body - 0.3,
            leading=leading - 1.0,
            textColor=HexColor(config.INK),
            spaceAfter=1.6,
        ),
        "footer": ParagraphStyle(
            "footer",
            fontName=BASE_FONT,
            fontSize=max(5.6, body - 2.0),
            leading=max(6.6, body - 1.0),
            textColor=HexColor(config.MUTED),
            spaceAfter=1.0,
        ),
        "flag": ParagraphStyle(
            "flag",
            fontName=BASE_FONT,
            fontSize=max(5.6, body - 2.0),
            leading=max(6.6, body - 1.0),
            textColor=HexColor(config.FLAG),
            spaceAfter=1.0,
        ),
    }


def _heading(text: str, styles: dict[str, Any]) -> list[Any]:
    """A section heading with the hairline rule under it."""
    return [
        Paragraph(escape(text).upper(), styles["heading"]),
        _Rule(HexColor(config.RULE)),
        Spacer(1, 2.5),
    ]


# --- Section 1: company snapshot --------------------------------------------


def _snapshot_section(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    width: float,
) -> list[Any]:
    """Six core metrics as a strip of labelled figures."""
    flow = _heading("1. Company snapshot", styles)
    cells = _snapshot_cells(profile)

    labels = [cell[0] for cell in cells]
    values = [cell[1] for cell in cells]
    size = styles["sizes"]["table"]

    label_style = ParagraphStyle(
        "snapshot-label",
        fontName=BASE_FONT,
        fontSize=size - 0.8,
        leading=size + 0.4,
        textColor=HexColor(config.MUTED),
        alignment=TA_CENTER,
    )
    value_style = ParagraphStyle(
        "snapshot-value",
        fontName=BOLD_FONT,
        fontSize=size + 3.4,
        leading=size + 5.0,
        textColor=HexColor(config.NAVY),
        alignment=TA_CENTER,
    )

    table = Table(
        [
            [Paragraph(escape(label).upper(), label_style) for label in labels],
            [Paragraph(escape(value), value_style) for value in values],
        ],
        colWidths=[width / len(cells)] * len(cells),
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), HexColor(config.BAND)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, 0), 4.0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0.5),
                ("TOPPADDING", (0, 1), (-1, 1), 0.5),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 4.5),
                ("LINEAFTER", (0, 0), (-2, -1), 0.4, HexColor(config.RULE)),
                ("BOX", (0, 0), (-1, -1), 0.4, HexColor(config.RULE)),
            ]
        )
    )
    flow.append(table)
    return flow


def _snapshot_cells(profile: CompanyProfile) -> list[tuple[str, str]]:
    """Pick the six metrics worth the space, by what the company actually is.

    A pre-revenue company's six most useful figures are not a SaaS company's, so
    the strip is chosen from what was disclosed rather than fixed in advance.
    Anything never disclosed shows an em dash rather than being dropped, so the
    reader can see the gap.
    """
    metrics = profile.metrics
    terms = profile.round
    cells: list[tuple[str, str]] = []

    if metrics.countable_revenue is not None:
        cells.append((metrics.revenue_label, fmt.fmt_money(metrics.countable_revenue)))
    else:
        cells.append(("Revenue", "Pre-revenue"))

    candidates: list[tuple[str, Any]] = [
        ("Growth", fmt.fmt_pct(metrics.growth_rate) if metrics.growth_rate is not None else None),
        (
            "Gross margin",
            fmt.fmt_pct(metrics.gross_margin) if metrics.gross_margin is not None else None,
        ),
        ("NRR", fmt.fmt_pct(metrics.nrr) if metrics.nrr is not None else None),
        (
            "Customers",
            fmt.fmt_number(metrics.customer_count)
            if metrics.customer_count is not None
            else None,
        ),
        (
            "Top customer",
            fmt.fmt_pct(metrics.largest_customer_pct)
            if metrics.largest_customer_pct is not None
            else None,
        ),
        ("Runway", fmt.fmt_months(metrics.runway_months) if metrics.runway_months else None),
        ("Burn", fmt.fmt_money(metrics.burn) if metrics.burn else None),
        ("TAM", fmt.fmt_money(metrics.tam) if metrics.tam else None),
        (
            "Patents",
            f"{_patent_count(profile):.0f}" if _patent_count(profile) else None,
        ),
    ]
    for label, value in candidates:
        if value is not None and len(cells) < 5:
            cells.append((label, value))

    # The raise always earns the last slot: it is the number the rest of the page
    # is about.
    while len(cells) < 5:
        for label, _ in candidates:
            if label not in {name for name, _ in cells}:
                cells.append((label, fmt.DASH))
                break
        else:
            break
    cells.append(("Raise", fmt.fmt_money(terms.raise_amount)))
    return cells[:6]


def _patent_count(profile: CompanyProfile) -> float:
    """Granted patents where there are any, filings otherwise."""
    granted = profile.quality.patents_granted or 0
    filed = profile.quality.patents_filed or 0
    return granted or filed


# --- Section 2: valuation range ---------------------------------------------


def _range_section(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    width: float,
) -> list[Any]:
    flow = _heading("2. Valuation range — estimated pre-money", styles)
    flow.append(_RangeBand(analysis, styles, width))
    return flow


class _RangeBand(Flowable):
    """The focal point: low / base / high, the ask, and the verdict.

    Drawn rather than typeset. The three figures sit over a track whose geometry
    is the range itself, with the requested valuation marked in place on it —
    which is what lets a reader see the size of the gap before reading its
    percentage.
    """

    def __init__(self, analysis: ValuationAnalysis, styles: dict[str, Any], width: float):
        super().__init__()
        self.analysis = analysis
        self.styles = styles
        self.width = width
        self.height = _BAND_HEIGHT

    def wrap(self, available_width: float, _available_height: float):
        self.width = available_width
        return available_width, self.height

    def draw(self) -> None:
        canvas = self.canv
        analysis = self.analysis

        panel = self.width * 0.34
        left_width = self.width - panel - 10

        canvas.setFillColor(HexColor(config.BAND))
        canvas.rect(0, 0, left_width, self.height, stroke=0, fill=1)
        canvas.setStrokeColor(HexColor(config.RULE))
        canvas.setLineWidth(0.4)
        canvas.rect(0, 0, left_width, self.height, stroke=1, fill=0)

        self._draw_figures(canvas, left_width)
        self._draw_track(canvas, left_width)
        self._draw_panel(canvas, left_width + 10, panel)

    def _draw_figures(self, canvas, left_width: float) -> None:
        valuation = self.analysis.range
        columns = (
            ("LOW", valuation.low, config.MUTED, 15.0),
            ("BASE", valuation.base, config.NAVY, 21.0),
            ("HIGH", valuation.high, config.MUTED, 15.0),
        )
        slot = left_width / 3
        top = self.height - 12

        for index, (label, value, color, size) in enumerate(columns):
            centre = slot * index + slot / 2
            canvas.setFont(BASE_FONT, 6.6)
            canvas.setFillColor(HexColor(config.MUTED))
            canvas.drawCentredString(centre, top, label)
            canvas.setFont(BOLD_FONT, size)
            canvas.setFillColor(HexColor(color))
            canvas.drawCentredString(centre, top - size - 2.5, fmt.fmt_money(value))

    def _draw_track(self, canvas, left_width: float) -> None:
        """The range as a bar, with the base tick and the requested marker on it."""
        analysis = self.analysis
        valuation = analysis.range
        inset = 16.0
        track_y = 15.0
        track_left = inset
        track_right = left_width - inset
        span = track_right - track_left
        if span <= 0 or valuation.high <= valuation.low:
            return

        requested = analysis.requested_valuation
        # The axis stretches to hold the ask when it falls outside the range,
        # so the marker is never pinned misleadingly to an end of the bar.
        axis_low = min(valuation.low, requested or valuation.low)
        axis_high = max(valuation.high, requested or valuation.high)
        axis_span = axis_high - axis_low
        if axis_span <= 0:
            return
        pad = axis_span * 0.08
        axis_low -= pad
        axis_high += pad
        axis_span = axis_high - axis_low

        def position(value: float) -> float:
            return track_left + span * (value - axis_low) / axis_span

        canvas.setStrokeColor(HexColor(config.RULE))
        canvas.setLineWidth(2.0)
        canvas.line(track_left, track_y, track_right, track_y)

        canvas.setStrokeColor(HexColor(config.ACCENT))
        canvas.setLineWidth(4.0)
        canvas.line(position(valuation.low), track_y, position(valuation.high), track_y)

        canvas.setStrokeColor(HexColor(config.NAVY))
        canvas.setLineWidth(1.4)
        base_x = position(valuation.base)
        canvas.line(base_x, track_y - 4.5, base_x, track_y + 4.5)

        if requested is None:
            return
        marker_x = position(requested)
        color = HexColor(config.STATUS_COLORS.get(analysis.requested_status, config.FLAG))
        canvas.setFillColor(color)
        canvas.setStrokeColor(color)
        size = 3.4
        path = canvas.beginPath()
        path.moveTo(marker_x, track_y + 7.0)
        path.lineTo(marker_x - size, track_y + 7.0 + size * 1.7)
        path.lineTo(marker_x + size, track_y + 7.0 + size * 1.7)
        path.close()
        canvas.drawPath(path, stroke=0, fill=1)
        canvas.setFont(BOLD_FONT, 6.2)
        label = f"ASK {fmt.fmt_money(requested)}"
        offset = -1 if marker_x > (track_left + track_right) / 2 else 1
        if offset > 0:
            canvas.drawString(marker_x + 5, track_y + 8.0, label)
        else:
            canvas.drawRightString(marker_x - 5, track_y + 8.0, label)

    def _draw_panel(self, canvas, x: float, panel: float) -> None:
        """The ask, the premium to base, and the status chip."""
        analysis = self.analysis
        canvas.setFillColor(HexColor(config.NAVY))
        canvas.rect(x, 0, panel, self.height, stroke=0, fill=1)

        inset = x + 9
        top = self.height - 11

        canvas.setFont(BASE_FONT, 6.4)
        canvas.setFillColor(HexColor("#8FA9C7"))
        basis = (analysis.requested_basis or "requested valuation").upper()
        canvas.drawString(inset, top, basis)

        canvas.setFont(BOLD_FONT, 15.0)
        canvas.setFillColor(HexColor(config.PAPER))
        canvas.drawString(inset, top - 16, fmt.fmt_money(analysis.requested_valuation))

        canvas.setFont(BASE_FONT, 6.4)
        canvas.setFillColor(HexColor("#8FA9C7"))
        canvas.drawString(inset, top - 29, "PREMIUM TO BASE")
        canvas.setFont(BOLD_FONT, 10.0)
        canvas.setFillColor(HexColor(config.PAPER))
        canvas.drawString(inset, top - 41, fmt.fmt_signed_pct(analysis.requested_gap_percent))

        status = analysis.requested_status or config.INSUFFICIENT
        color = HexColor(config.STATUS_COLORS.get(status, config.MUTED))
        chip_height = 13.0
        canvas.setFillColor(color)
        canvas.rect(inset, 8.0, panel - 18, chip_height, stroke=0, fill=1)

        size, text = 6.8, status
        while size > 4.8 and canvas.stringWidth(text, BOLD_FONT, size) > panel - 24:
            size -= 0.2
        canvas.setFont(BOLD_FONT, size)
        canvas.setFillColor(HexColor(config.PAPER))
        canvas.drawCentredString(
            inset + (panel - 18) / 2, 8.0 + chip_height / 2 - size / 2 + 0.6, text
        )


# --- Section 3: valuation methods -------------------------------------------


def _methods_section(
    analysis: ValuationAnalysis, styles: dict[str, Any], width: float
) -> list[Any]:
    flow = _heading("3. Valuation methods", styles)
    rows = [["Method", "Valuation", "Weight", "Basis"]]
    for method in analysis.used_methods:
        rows.append(
            [
                fmt.method_label(method.name),
                fmt.fmt_money(method.base),
                fmt.fmt_pct(method.weight),
                method.basis,
            ]
        )
    if len(rows) == 1:
        rows.append(["No method could be grounded", fmt.DASH, fmt.DASH, ""])
    else:
        rows.append(
            [
                "Weighted base valuation",
                fmt.fmt_money(analysis.range.base),
                "100%",
                f"after {fmt.fmt_signed_pct(analysis.net_adjustment)} net adjustment",
            ]
        )

    flow.append(_table(rows, styles, width, (0.26, 0.15, 0.11, 0.48), total_row=True))

    excluded = [
        f"{fmt.method_label(method.name)} — {method.exclusion_reason}"
        for method in analysis.excluded_methods
        if method.exclusion_reason and "does not nominate it" not in method.exclusion_reason
    ]
    if excluded:
        flow.append(
            Paragraph(
                "<b>Excluded:</b> " + "  ".join(escape(item) for item in excluded[:2]),
                styles["small"],
            )
        )
    return flow


# --- Section 4: premium / discount drivers ----------------------------------


def _drivers_section(
    analysis: ValuationAnalysis, styles: dict[str, Any], width: float
) -> list[Any]:
    """The bridge, as two columns of signed factors plus the reconciling line."""
    flow = _heading("4. Premium / discount drivers", styles)
    share = analysis.adjustment_applied_share
    # With no share of the blend open to them, every factor would print as "+0%",
    # which reads as a rendering fault rather than as the deliberate absence of
    # double counting. The factors are still worth listing; the percentages are not.
    quantified = share >= 0.005

    def render(items, color: str, sign: str) -> list[Any]:
        cells: list[Any] = []
        for item in items[: config.MAX_PRINTED_FACTORS]:
            effective = item.signed_pct * share
            marker = f"{sign}{abs(effective) * 100:.0f}%" if quantified else sign
            cells.append(
                Paragraph(
                    f'<font color="{color}"><b>{marker}</b></font> '
                    f"{escape(item.label)} — {escape(fmt.truncate_words(item.rationale, 16))}",
                    styles["driver"],
                )
            )
        if not cells:
            cells.append(
                Paragraph(
                    "None evidenced in the material supplied.",
                    styles["small"],
                )
            )
        return cells

    header_style = ParagraphStyle(
        "driver-header",
        fontName=BOLD_FONT,
        fontSize=styles["sizes"]["table"],
        leading=styles["sizes"]["table"] + 2,
        textColor=HexColor(config.PAPER),
    )
    premium = [Paragraph("PREMIUM", header_style)] + render(
        analysis.premiums(), config.POSITIVE, "+"
    )
    discount = [Paragraph("DISCOUNT", header_style)] + render(
        analysis.discounts(), config.FLAG, "−"
    )

    half = (width - 10) / 2
    table = Table([[premium, discount]], colWidths=[half, half], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    flow.append(table)
    flow.append(_bridge_line(analysis, styles))
    return flow


def _bridge_line(analysis: ValuationAnalysis, styles: dict[str, Any]) -> Paragraph:
    """The section 8 bridge in one reconciling sentence."""
    if analysis.baseline is None:
        return Paragraph("No baseline was established.", styles["small"])

    share = analysis.adjustment_applied_share
    baseline = fmt.fmt_money(analysis.baseline.base)
    base = fmt.fmt_money(analysis.range.base)

    if share < 0.005:
        card = analysis.method("scorecard")
        multiplier = (card.inputs.get("multiplier") if card else None) or 1.0
        return Paragraph(
            f"<b>Bridge:</b> the scorecard is the only method that ran, and it already prices "
            f"these factors into its {multiplier:.2f}x quality multiplier. They are listed "
            f"above rather than applied a second time, so the base valuation is the method's "
            f"own answer of <b>{base}</b>.",
            styles["small"],
        )

    text = (
        f"<b>Bridge:</b> weighted method baseline {baseline} "
        f"{fmt.fmt_signed_pct(analysis.net_adjustment)} net adjustment = "
        f"<b>{base}</b> base valuation."
    )
    if share < 0.999:
        text += (
            f" Factors reach {fmt.fmt_pct(share)} of the blend; the scorecard's share already "
            "prices quality into its own answer."
        )
    return Paragraph(text, styles["small"])


# --- Section 5: round and dilution ------------------------------------------


def _round_section(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    width: float,
) -> list[Any]:
    flow = _heading("5. Round & dilution", styles)
    rows = [["Case", "Pre-money", "New capital", "Post-money", "New investor own."]]
    raise_amount = profile.round.raise_amount

    for case in analysis.dilution:
        rows.append(
            [
                case.label,
                fmt.fmt_money(case.pre_money),
                fmt.fmt_money(raise_amount),
                fmt.fmt_money(case.post_money),
                fmt.fmt_pct(case.new_investor_pct, 1),
            ]
        )
    if len(rows) == 1:
        rows.append(["No round disclosed", fmt.DASH, fmt.DASH, fmt.DASH, fmt.DASH])

    flow.append(_table(rows, styles, width, (0.22, 0.20, 0.19, 0.19, 0.20)))

    notes = [dilution_model.instrument_note(profile), dilution_model.committed_note(profile)]
    requested = analysis.dilution_case(dilution_model.REQUESTED)
    if requested is not None and requested.note:
        notes.append(requested.note)
    line = "  ·  ".join(note for note in notes if note)
    if line:
        flow.append(Paragraph(escape(line), styles["small"]))
    return flow


# --- Section 6: sensitivity and milestone -----------------------------------


def _closing_section(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    width: float,
    budget: int,
) -> list[Any]:
    flow = _heading("6. Sensitivity & key milestone", styles)

    grid_width = width * 0.46
    text_width = width - grid_width - 12

    left: list[Any] = []
    if analysis.sensitivity.present:
        left.append(_sensitivity_table(analysis, styles, grid_width))
        left.append(Paragraph(escape(analysis.sensitivity.note), styles["note"]))
    else:
        left.append(
            Paragraph(
                "No sensitivity grid could be built from the information supplied.",
                styles["small"],
            )
        )

    right: list[Any] = []
    if analysis.key_milestone:
        right.append(
            Paragraph(f"<b>{escape(analysis.key_milestone)}</b>", styles["body"])
        )
    factors = analysis.decision_factors[:3]
    if factors:
        right.append(
            Paragraph(
                "<b>What decides acceptance:</b> "
                + "; ".join(escape(fmt.truncate_words(item, 14)) for item in factors)
                + ".",
                styles["small"],
            )
        )
    comparables = comparable_financing.summary_lines(profile, limit=2)
    if comparables:
        right.append(
            Paragraph(
                "<b>Comparables:</b> " + " · ".join(escape(line) for line in comparables),
                styles["small"],
            )
        )
    else:
        right.append(Paragraph(f"<b>{escape(config.NO_COMPARABLES)}</b>", styles["small"]))

    table = Table([[left, right]], colWidths=[grid_width, text_width], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (1, 0), (1, 0), 6),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    flow.append(table)
    return flow


def _sensitivity_table(
    analysis: ValuationAnalysis, styles: dict[str, Any], width: float
) -> Table:
    grid = analysis.sensitivity
    rows = [[grid.row_axis] + list(grid.column_labels)]
    for label, cells in grid.rows:
        rows.append([label] + list(cells))

    columns = len(rows[0])
    first = 0.30
    proportions = tuple([first] + [(1 - first) / (columns - 1)] * (columns - 1))
    table = _table(rows, styles, width, proportions)

    if grid.base_cell is not None:
        row, column = grid.base_cell
        cell = (column + 1, row + 1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", cell, cell, HexColor("#DCE7F2")),
                    ("BOX", cell, cell, 0.9, HexColor(config.NAVY)),
                ]
            )
        )
    return table


# --- Shared table -----------------------------------------------------------


def _table(
    rows: list[list[str]],
    styles: dict[str, Any],
    width: float,
    proportions: tuple[float, ...],
    total_row: bool = False,
) -> Table:
    """A compact table with a navy header row and hairline row rules.

    Cells are Paragraphs rather than plain strings so a long basis line wraps
    inside its column instead of running silently over its neighbour.
    """
    size = styles["sizes"]["table"]
    widths = [width * proportion for proportion in proportions]
    cells = [
        [
            Paragraph(
                escape(str(text)),
                _cell_style(size, header=row_index == 0, first=column_index == 0),
            )
            for column_index, text in enumerate(row)
        ]
        for row_index, row in enumerate(rows)
    ]
    table = Table(cells, colWidths=widths, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(config.NAVY)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor(config.PAPER), HexColor(config.BAND)]),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, HexColor(config.RULE)),
        ("BOX", (0, 0), (-1, -1), 0.4, HexColor(config.RULE)),
    ]
    if total_row and len(rows) > 2:
        commands += [
            ("LINEABOVE", (0, -1), (-1, -1), 0.9, HexColor(config.NAVY)),
            ("BACKGROUND", (0, -1), (-1, -1), HexColor("#E7EDF4")),
        ]
    table.setStyle(TableStyle(commands))
    return table


def _cell_style(size: float, header: bool, first: bool) -> ParagraphStyle:
    """Header rows are white on navy; the first column labels, the rest are figures."""
    return ParagraphStyle(
        f"cell-{int(header)}-{int(first)}-{size}",
        fontName=BOLD_FONT if header or first else BASE_FONT,
        fontSize=size,
        leading=size + 1.6,
        textColor=HexColor(config.PAPER if header else config.INK),
        alignment=TA_LEFT if first else TA_RIGHT,
    )


class _Rule(Flowable):
    """A hairline rule under a section heading."""

    def __init__(self, color, thickness: float = 0.7):
        super().__init__()
        self.color = color
        self.thickness = thickness
        self.width = 0.0
        self.height = thickness
        self.keepWithNext = True

    def wrap(self, available_width: float, _available_height: float):
        self.width = available_width
        return available_width, self.height

    def draw(self) -> None:
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


# --- Page furniture ---------------------------------------------------------


def _footer_height(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    width: float,
) -> float:
    """Size the footer band to the confidence line, flags and disclaimer it carries."""
    measured = _measure(_footer_flowables(profile, analysis, styles), width - 16) + 24.0
    return min(max(measured, _MIN_FOOTER), _MAX_FOOTER)


def _footer_flowables(
    profile: CompanyProfile, analysis: ValuationAnalysis, styles: dict[str, Any]
) -> list[Any]:
    """Confidence, analyst flags, what was missing, and the disclaimer."""
    flow: list[Any] = []
    color = {
        config.HIGH: config.POSITIVE,
        config.MEDIUM: config.WARN,
        config.LOW: config.FLAG,
    }[analysis.confidence]
    flow.append(
        Paragraph(
            f'<b>VALUATION CONFIDENCE: <font color="{color}">{analysis.confidence}</font></b>'
            f"  {escape(fmt.truncate_words(analysis.confidence_reason, 34))}",
            styles["footer"],
        )
    )

    flags = _flags(analysis)
    if flags:
        flow.append(Paragraph(f"<b>FLAGS</b>  {escape(flags)}", styles["flag"]))

    if profile.missing_information:
        missing = "; ".join(profile.missing_information[:6])
        remaining = len(profile.missing_information) - 6
        if remaining > 0:
            missing += f" (+{remaining} more in the JSON record)"
        flow.append(
            Paragraph(f"<b>NOT DISCLOSED</b>  {escape(missing)}", styles["footer"])
        )

    flow.append(Paragraph(escape(config.DISCLAIMER), styles["footer"]))
    return flow


def _flags(analysis: ValuationAnalysis) -> str:
    """One line per distinct rule, most severe first."""
    seen: set[str] = set()
    messages: list[str] = []
    for issue in analysis.issues:
        if issue.severity == "NOTE" or issue.rule in seen:
            continue
        seen.add(issue.rule)
        messages.append(f"▲ {fmt.truncate_words(issue.message, 22)}")
    if not messages:
        return ""
    line = "  ".join(messages[:3])
    if len(messages) > 3:
        line += f"  (+{len(messages) - 3} more in the JSON record)"
    return line


def _page_furniture(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    stamp: date,
    footer_height: float,
):
    """Return an onPage callback painting the header band and the footer band."""

    def draw(canvas, _doc):
        canvas.saveState()
        _draw_header(canvas, profile, stamp)
        _draw_footer(canvas, profile, analysis, styles, footer_height)
        canvas.restoreState()

    return draw


def _fit(canvas, text: str, font: str, size: float, max_width: float, floor: float):
    """Shrink then ellipsize `text` until it fits `max_width`."""
    while size > floor and canvas.stringWidth(text, font, size) > max_width:
        size -= 0.2
    if canvas.stringWidth(text, font, size) <= max_width:
        return text, size
    while text and canvas.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text.rstrip(" ·,;-") + "…", size


def _draw_header(canvas, profile: CompanyProfile, stamp: date) -> None:
    """Paint the navy header: firm, company, and the identity strip."""
    margin = config.PAGE_MARGIN * inch
    height = config.HEADER_HEIGHT * inch
    top = PAGE_HEIGHT - margin
    bottom = top - height
    width = PAGE_WIDTH - 2 * margin

    canvas.setFillColor(HexColor(config.NAVY))
    canvas.rect(margin, bottom, width, height, stroke=0, fill=1)

    left = margin + 12
    right = PAGE_WIDTH - margin - 12

    canvas.setFont(BOLD_FONT, 7.4)
    canvas.setFillColor(HexColor("#8FA9C7"))
    canvas.drawString(left, top - 14, config.FIRM_NAME)
    canvas.setFont(BASE_FONT, 7.4)
    canvas.drawRightString(right, top - 14, stamp.strftime("%d %B %Y"))

    name = (profile.identity.name or "Company").upper()
    name, size = _fit(canvas, name, BOLD_FONT, 20.0, width - 24, floor=11.0)
    canvas.setFont(BOLD_FONT, size)
    canvas.setFillColor(HexColor(config.PAPER))
    canvas.drawString(left, top - 16 - size, name)

    identity = fmt.join_facts(
        config.DOC_TITLE.title(),
        profile.identity.sector or profile.identity.subsector,
        profile.identity.stage_text or config.STAGE_LABELS[profile.stage],
        profile.identity.geography,
    )
    identity, size = _fit(canvas, identity, BASE_FONT, 8.0, width - 24, floor=5.8)
    canvas.setFont(BASE_FONT, size)
    canvas.setFillColor(HexColor("#B9C6D8"))
    canvas.drawString(left, bottom + 8, identity)


def _draw_footer(
    canvas,
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    styles: dict[str, Any],
    footer_height: float,
) -> None:
    """Paint the footer band, its rule, the confidence block and the branding line."""
    margin = config.PAGE_MARGIN * inch
    width = PAGE_WIDTH - 2 * margin

    canvas.setFillColor(HexColor(config.BAND))
    canvas.rect(margin, margin, width, footer_height, stroke=0, fill=1)
    canvas.setStrokeColor(HexColor(config.NAVY))
    canvas.setLineWidth(1.2)
    canvas.line(margin, margin + footer_height, margin + width, margin + footer_height)

    frame = Frame(
        margin + 8,
        margin + 12,
        width - 16,
        max(footer_height - 20, 12),
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    frame.addFromList(_footer_flowables(profile, analysis, styles), canvas)

    canvas.setFont(BASE_FONT, 6.4)
    canvas.setFillColor(HexColor(config.MUTED))
    canvas.drawString(margin + 8, margin + 4.5, config.CONFIDENTIALITY)

    if config.FIRM_LOGO.exists():
        try:
            image = ImageReader(str(config.FIRM_LOGO))
            image_width, image_height = image.getSize()
            draw_height = 11.0
            draw_width = image_width * (draw_height / image_height)
            canvas.drawImage(
                image,
                PAGE_WIDTH - margin - 8 - draw_width,
                margin + 3.0,
                width=draw_width,
                height=draw_height,
                mask="auto",
                preserveAspectRatio=True,
            )
        except Exception:
            pass
