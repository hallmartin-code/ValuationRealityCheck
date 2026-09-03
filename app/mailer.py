"""Emails each completed valuation to the desk inbox via Resend.

Delivery is deliberately best-effort: a send that fails must never cost the
caller the analysis it just spent a deck-sized model call producing. Every entry
point returns an outcome dict — `{"sent": bool, "id": str, "error": str,
"skipped": str}` — rather than raising, and the caller decides how loudly to
surface it.

The body carries the answer, not a link to it: the range, the ask, the gap, the
methods and any analyst flags, so a partner can read the conclusion on a phone
and open the attached PDF only when they want the page itself. The figures are
formatted by the same helpers the PDF uses, so the email and the page can never
quote different numbers.
"""

from __future__ import annotations

import base64
import html
import os
from datetime import date
from typing import Any

from . import config
from .models.company import CompanyProfile
from .models.valuation import ValuationAnalysis
from .utils import formatting as fmt

DEFAULT_TO = "Info@tencapital.group"

# The Resend sandbox sender. It only delivers to the Resend account owner, so a
# real deploy sets RESEND_FROM to an address on a domain verified at
# resend.com/domains — otherwise mail to the desk inbox is accepted and dropped.
DEFAULT_FROM = "TEN Capital Valuation <onboarding@resend.dev>"

_OFF = {"0", "false", "no", "off"}


def is_enabled() -> bool:
    """True when a Resend key is present and delivery has not been switched off."""
    if os.getenv("EMAIL_REPORTS", "1").strip().lower() in _OFF:
        return False
    return bool((os.getenv("RESEND_API_KEY") or "").strip())


def recipients() -> list[str]:
    """Addresses the analysis is delivered to, comma-separated in REPORT_EMAIL_TO."""
    raw = (os.getenv("REPORT_EMAIL_TO") or DEFAULT_TO).strip()
    return [address.strip() for address in raw.split(",") if address.strip()]


def sender() -> str:
    """The From address. Its domain must be verified in Resend."""
    return (os.getenv("RESEND_FROM") or DEFAULT_FROM).strip()


# --- Content ----------------------------------------------------------------


def _headline(profile: CompanyProfile, analysis: ValuationAnalysis) -> list[tuple[str, str]]:
    """The six figures that answer the valuation question."""
    return [
        ("Estimated pre-money", fmt.fmt_range(analysis.range.low, analysis.range.high)),
        ("Base case", fmt.fmt_money(analysis.range.base)),
        (
            fmt.titlecase(analysis.requested_basis or "Requested"),
            fmt.fmt_money(analysis.requested_valuation),
        ),
        ("Premium to base", fmt.fmt_signed_pct(analysis.requested_gap_percent)),
        ("Raise", fmt.fmt_money(profile.round.raise_amount)),
        ("Confidence", analysis.confidence),
    ]


def _run_facts(
    profile: CompanyProfile, analysis: ValuationAnalysis, meta: dict[str, Any]
) -> list[tuple[str, str]]:
    """How the analysis was produced, for anyone auditing the run."""
    ledger = profile.ledger
    facts = [
        ("Source deck", str(meta.get("source", profile.deck_source or "—"))),
        ("Slides read", str(meta.get("slides", profile.slide_count or "—"))),
        ("Stage band", config.STAGE_LABELS[profile.stage]),
        ("Instrument", profile.round.instrument or config.NOT_DISCLOSED),
        (
            "Provenance",
            f"{len(ledger.by_type(config.USER))} user · "
            f"{len(ledger.by_type(config.DECK))} deck · "
            f"{len(ledger.by_type(config.COMPARABLE))} comparable · "
            f"{len(ledger.by_type(config.CALCULATED))} calculated · "
            f"{len(ledger.assumptions())} assumption",
        ),
        ("Evidence", analysis.comparable_note),
        ("Model", str(meta.get("model", config.LLM_MODEL))),
    ]
    if meta.get("seconds"):
        facts.append(("Run time", f"{meta['seconds']:.0f}s"))
    return facts


def _flags(analysis: ValuationAnalysis) -> list[Any]:
    """One finding per distinct rule, matching what the PDF's flag band shows."""
    seen: set[str] = set()
    flags = []
    for issue in analysis.issues:
        if issue.severity not in ("ERROR", "WARNING") or issue.rule in seen:
            continue
        seen.add(issue.rule)
        flags.append(issue)
    return flags


def build_subject(profile: CompanyProfile, analysis: ValuationAnalysis) -> str:
    """Subject line: company, the range, the verdict, and whether anything is flagged."""
    company = profile.identity.name or "Unnamed company"
    parts = [f"Valuation — {company}"]
    if analysis.range.base:
        parts.append(fmt.fmt_range(analysis.range.low, analysis.range.high))
    if analysis.requested_status:
        parts.append(analysis.requested_status.title())
    errors = len(analysis.errors())
    if errors:
        parts.append(f"{errors} error{'s' if errors != 1 else ''}")
    return " · ".join(parts)


def build_text(
    profile: CompanyProfile, analysis: ValuationAnalysis, meta: dict[str, Any]
) -> str:
    """Plain-text alternative, for clients that will not render HTML."""
    lines = [
        build_subject(profile, analysis),
        "",
        fmt.join_facts(
            profile.identity.sector,
            profile.identity.stage_text or config.STAGE_LABELS[profile.stage],
            profile.identity.geography,
        ),
        "",
    ]
    lines += [f"{label}: {value}" for label, value in _headline(profile, analysis)]
    lines += [
        "",
        f"VERDICT: {analysis.requested_status or config.INSUFFICIENT}",
        "",
        "ASSESSMENT",
        analysis.requested_statement or "—",
        "",
    ]

    lines.append("VALUATION METHODS")
    for method in analysis.used_methods:
        lines.append(
            f"- {fmt.method_label(method.name)}: {fmt.fmt_money(method.base)} "
            f"at {fmt.fmt_pct(method.weight)} — {method.basis}"
        )
    if analysis.baseline:
        lines.append(
            f"Bridge: {fmt.fmt_money(analysis.baseline.base)} baseline "
            f"{fmt.fmt_signed_pct(analysis.net_adjustment)} = "
            f"{fmt.fmt_money(analysis.range.base)}"
        )
    lines.append("")

    lines.append("PREMIUM")
    lines += [
        f"+ {fmt.fmt_pct(item.pct)} {item.label}" for item in analysis.premiums()[:4]
    ] or ["None evidenced."]
    lines.append("")
    lines.append("DISCOUNT")
    lines += [
        f"- {fmt.fmt_pct(item.pct)} {item.label}" for item in analysis.discounts()[:4]
    ] or ["None evidenced."]
    lines.append("")

    lines.append("ROUND & DILUTION")
    for case in analysis.dilution:
        lines.append(
            f"{case.label}: {fmt.fmt_money(case.pre_money)} pre → "
            f"{fmt.fmt_money(case.post_money)} post, "
            f"{fmt.fmt_pct(case.new_investor_pct, 1)} to new investors"
        )
    lines.append("")

    if analysis.key_milestone:
        lines += ["KEY MILESTONE", analysis.key_milestone, ""]

    flags = _flags(analysis)
    lines.append("ANALYST FLAGS")
    lines += [f"[{issue.severity}] {issue.message}" for issue in flags] or ["None."]
    lines.append("")

    if profile.missing_information:
        lines += ["NOT DISCLOSED", "; ".join(profile.missing_information), ""]

    lines += [f"{label}: {value}" for label, value in _run_facts(profile, analysis, meta)]
    lines += [
        "",
        "The one-page PDF and the full JSON audit trail are attached.",
        config.DISCLAIMER,
        f"{config.FIRM_NAME} · {config.CONFIDENTIALITY}",
    ]
    return "\n".join(lines)


# Status colours for the verdict pill, matching the chip on the PDF.
_STATUS_STYLE = {
    config.BELOW_RANGE: ("#E8F1F8", "#1F6FB2"),
    config.WITHIN_RANGE: ("#E7F4EE", "#1D6B4F"),
    config.ABOVE_RANGE: ("#FDF3E3", "#8A5314"),
    config.MATERIAL_GAP: ("#FBEDEA", "#A5402A"),
}


def build_html(
    profile: CompanyProfile, analysis: ValuationAnalysis, meta: dict[str, Any]
) -> str:
    """Brand-styled HTML body carrying the conclusion, not just a link.

    Table-based and inline-styled on purpose: email clients strip <style>
    blocks, flexbox and grid.
    """
    esc = html.escape
    company = esc(profile.identity.name or "Unnamed company")
    tag = esc(
        fmt.join_facts(
            profile.identity.sector,
            profile.identity.stage_text or config.STAGE_LABELS[profile.stage],
            profile.identity.geography,
        )
    )

    band = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F2F4F7;border:1px solid #E3E8EF;border-radius:8px;'
        'margin:0 0 16px;"><tr>'
        + "".join(
            f'<td width="33%" align="center" style="padding:14px 8px;">'
            f'<div style="font:600 9px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.16em;'
            f'text-transform:uppercase;color:#5A6472;">{esc(label)}</div>'
            f'<div style="font:700 {size}px/1.3 Helvetica,Arial,sans-serif;color:{colour};'
            f'margin-top:4px;">{esc(value)}</div></td>'
            for label, value, size, colour in (
                ("Low", fmt.fmt_money(analysis.range.low), 17, "#5A6472"),
                ("Base", fmt.fmt_money(analysis.range.base), 24, "#0A2342"),
                ("High", fmt.fmt_money(analysis.range.high), 17, "#5A6472"),
            )
        )
        + "</tr></table>"
    )

    status = analysis.requested_status or config.INSUFFICIENT
    background, ink = _STATUS_STYLE.get(status, ("#F2F4F7", "#5A6472"))
    ask = (
        f'<div style="font:400 13px/1.6 Helvetica,Arial,sans-serif;color:#1B1F27;'
        f'margin:0 0 14px;">Asking <b>{esc(fmt.fmt_money(analysis.requested_valuation))}</b> '
        f'({esc(analysis.requested_basis or "not disclosed")}) · '
        f'<b>{esc(fmt.fmt_signed_pct(analysis.requested_gap_percent))}</b> to base &nbsp;'
        f'<span style="display:inline-block;padding:3px 10px;border-radius:99px;'
        f'background:{background};color:{ink};font:700 10px/1.5 Helvetica,Arial,sans-serif;'
        f'letter-spacing:.06em;">{esc(status)}</span></div>'
    )

    headline = _headline(profile, analysis)
    stats = "".join(
        "<tr>"
        + "".join(
            f'<td width="33%" style="padding:0 10px 12px 0;vertical-align:top;">'
            f'<div style="font:600 9px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.09em;'
            f'text-transform:uppercase;color:#8A8F98;">{esc(label)}</div>'
            f'<div style="font:700 15px/1.3 Helvetica,Arial,sans-serif;color:#0A2342;'
            f'margin-top:2px;">{esc(value)}</div></td>'
            for label, value in headline[index : index + 3]
        )
        + "</tr>"
        for index in range(0, len(headline), 3)
    )

    methods = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;font-size:12px;color:#1B1F27;">'
        f"{esc(fmt.method_label(method.name))}</td>"
        f'<td align="right" style="padding:3px 12px 3px 0;font-size:12px;font-weight:600;'
        f'color:#0A2342;white-space:nowrap;">{esc(fmt.fmt_money(method.base))}</td>'
        f'<td align="right" style="padding:3px 0;font-size:12px;color:#5A6472;'
        f'white-space:nowrap;">{esc(fmt.fmt_pct(method.weight))}</td></tr>'
        for method in analysis.used_methods
    ) or '<tr><td style="font-size:12px;color:#5A6472;">No method could be grounded.</td></tr>'

    def drivers(items: list[Any], colour: str, sign: str) -> str:
        if not items:
            return '<li style="color:#8A8F98;">None evidenced.</li>'
        return "".join(
            f'<li style="margin:0 0 4px;"><b style="color:{colour};">{sign}'
            f"{esc(fmt.fmt_pct(item.pct))}</b> {esc(item.label)}</li>"
            for item in items[:4]
        )

    dilution = "".join(
        f'<tr><td style="padding:3px 12px 3px 0;font-size:12px;color:#1B1F27;">'
        f"{esc(case.label)}</td>"
        f'<td align="right" style="padding:3px 12px 3px 0;font-size:12px;color:#5A6472;'
        f'white-space:nowrap;">{esc(fmt.fmt_money(case.pre_money))}</td>'
        f'<td align="right" style="padding:3px 12px 3px 0;font-size:12px;color:#5A6472;'
        f'white-space:nowrap;">{esc(fmt.fmt_money(case.post_money))}</td>'
        f'<td align="right" style="padding:3px 0;font-size:12px;font-weight:600;'
        f'color:#0A2342;white-space:nowrap;">'
        f"{esc(fmt.fmt_pct(case.new_investor_pct, 1))}</td></tr>"
        for case in analysis.dilution
    )

    facts = "".join(
        f'<tr><td style="padding:2px 16px 2px 0;color:#7E90A8;font-size:12px;'
        f'white-space:nowrap;vertical-align:top;">{esc(label)}</td>'
        f'<td style="padding:2px 0;color:#1B1F27;font-size:12px;font-weight:600;">'
        f"{esc(value)}</td></tr>"
        for label, value in _run_facts(profile, analysis, meta)
    )

    flags = _flags(analysis)
    if flags:
        items = "".join(
            f'<li style="margin:0 0 4px;color:'
            f'{"#A5402A" if issue.severity == "ERROR" else "#7A5A1E"};">'
            f"<b>{esc(issue.severity)}</b> {esc(issue.message)}</li>"
            for issue in flags
        )
        flag_block = (
            '<div style="margin:0 0 16px;padding:11px 13px;border-radius:6px;'
            'background:#FDF3E3;border-left:3px solid #F3A22A;'
            'font:400 12px/1.5 Helvetica,Arial,sans-serif;">'
            '<div style="font-weight:700;color:#7A5A1E;margin-bottom:6px;">Analyst flags</div>'
            f'<ul style="margin:0;padding-left:16px;">{items}</ul></div>'
        )
    else:
        flag_block = (
            '<div style="margin:0 0 16px;padding:11px 13px;border-radius:6px;'
            'background:#EAF7F6;border-left:3px solid #35BEBB;color:#1D6360;'
            'font:400 12px/1.5 Helvetica,Arial,sans-serif;">'
            "Validation raised no errors or warnings.</div>"
        )

    def block(heading: str, body: str) -> str:
        return (
            '<tr><td style="padding:14px 0 0;">'
            f'<div style="font:600 10px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.09em;'
            f'text-transform:uppercase;color:#0A2342;">{esc(heading)}</div>'
            '<div style="height:1px;background:#E3E8EF;margin:5px 0 8px;"></div>'
            f'<div style="font:400 13px/1.55 Helvetica,Arial,sans-serif;color:#1B1F27;">'
            f"{body}</div></td></tr>"
        )

    sections = block("Assessment", esc(analysis.requested_statement or "—"))
    sections += block(
        "Valuation methods",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        + methods
        + "</table>"
        + (
            f'<div style="margin-top:7px;font-size:11.5px;color:#5A6472;">Bridge: '
            f"{esc(fmt.fmt_money(analysis.baseline.base))} baseline "
            f"{esc(fmt.fmt_signed_pct(analysis.net_adjustment))} net adjustment = "
            f"<b>{esc(fmt.fmt_money(analysis.range.base))}</b></div>"
            if analysis.baseline
            else ""
        ),
    )
    sections += block(
        "Premium / discount drivers",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td width="50%" style="vertical-align:top;padding-right:10px;">'
        '<div style="font:600 9px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.12em;'
        'text-transform:uppercase;color:#8A8F98;margin-bottom:5px;">Premium</div>'
        f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.5;">'
        f"{drivers(analysis.premiums(), '#1D6B4F', '+')}</ul></td>"
        '<td width="50%" style="vertical-align:top;">'
        '<div style="font:600 9px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.12em;'
        'text-transform:uppercase;color:#8A8F98;margin-bottom:5px;">Discount</div>'
        f'<ul style="margin:0;padding-left:16px;font-size:12px;line-height:1.5;">'
        f"{drivers(analysis.discounts(), '#A5402A', '−')}</ul></td></tr></table>",
    )
    if dilution:
        sections += block(
            "Round &amp; dilution",
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            '<tr><td style="font-size:9px;letter-spacing:.1em;text-transform:uppercase;'
            'color:#8A8F98;padding-bottom:4px;">Case</td>'
            '<td align="right" style="font-size:9px;letter-spacing:.1em;'
            'text-transform:uppercase;color:#8A8F98;padding-bottom:4px;">Pre</td>'
            '<td align="right" style="font-size:9px;letter-spacing:.1em;'
            'text-transform:uppercase;color:#8A8F98;padding-bottom:4px;">Post</td>'
            '<td align="right" style="font-size:9px;letter-spacing:.1em;'
            'text-transform:uppercase;color:#8A8F98;padding-bottom:4px;">New inv.</td></tr>'
            + dilution
            + "</table>",
        )
    if analysis.key_milestone:
        sections += block("Key milestone", esc(analysis.key_milestone))
    if profile.missing_information:
        sections += block(
            "Not disclosed",
            '<span style="color:#5A6472;font-size:12px;">'
            + esc("; ".join(profile.missing_information[:10]))
            + "</span>",
        )

    return f"""\
<!doctype html>
<html><body style="margin:0;padding:24px 12px;background:#F2F4F7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:#FFFFFF;border:1px solid #E3E8EF;border-radius:10px;overflow:hidden;">
  <tr><td style="background:#0A2342;padding:22px 26px;">
    <div style="font:700 10px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.13em;text-transform:uppercase;color:#8FA9C7;">{esc(config.FIRM_NAME)}</div>
    <div style="font:700 19px/1.3 Helvetica,Arial,sans-serif;color:#FFFFFF;margin-top:6px;">{company}</div>
    <div style="font:400 12px/1.5 Helvetica,Arial,sans-serif;color:#B9C6D8;margin-top:5px;">{esc(config.DOC_TITLE.title())}{" · " + tag if tag else ""}</div>
  </td></tr>
  <tr><td style="padding:22px 26px 0;">
    {band}
    {ask}
    {flag_block}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{stats}</table>
  </td></tr>
  <tr><td style="padding:0 26px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{sections}</table></td></tr>
  <tr><td style="padding:18px 26px 0;">
    <div style="font:600 10px/1.4 Helvetica,Arial,sans-serif;letter-spacing:.09em;text-transform:uppercase;color:#5A6472;">Run summary</div>
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:9px;font-family:Helvetica,Arial,sans-serif;">{facts}</table>
  </td></tr>
  <tr><td style="padding:20px 26px 24px;">
    <div style="height:1px;background:#E3E8EF;margin-bottom:14px;"></div>
    <div style="font:400 11px/1.6 Helvetica,Arial,sans-serif;color:#8A8F98;">
      The one-page PDF and the full JSON audit trail are attached.<br>
      {esc(config.DISCLAIMER)}<br>
      {esc(config.FIRM_NAME)} · {esc(config.CONFIDENTIALITY)}
    </div>
  </td></tr>
</table>
</body></html>"""


# --- Delivery ---------------------------------------------------------------


def send_analysis(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    pdf_bytes: bytes,
    json_bytes: bytes | None = None,
    stem: str = "valuation",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Email the analysis with its PDF and audit trail. Never raises."""
    meta = dict(meta or {})
    meta.setdefault("model", config.LLM_MODEL)
    meta.setdefault("date", date.today().isoformat())

    if not is_enabled():
        reason = (
            "delivery disabled via EMAIL_REPORTS"
            if (os.getenv("RESEND_API_KEY") or "").strip()
            else "RESEND_API_KEY is not set"
        )
        return {"sent": False, "skipped": reason}

    to = recipients()
    if not to:
        return {"sent": False, "skipped": "REPORT_EMAIL_TO is empty"}

    attachments = [
        {
            "filename": f"{stem}.pdf",
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
            "content_type": "application/pdf",
        }
    ]
    if json_bytes:
        attachments.append(
            {
                "filename": f"{stem}.json",
                "content": base64.b64encode(json_bytes).decode("ascii"),
                "content_type": "application/json",
            }
        )

    try:
        import resend

        resend.api_key = os.environ["RESEND_API_KEY"].strip()
        response = resend.Emails.send(
            {
                "from": sender(),
                "to": to,
                "subject": build_subject(profile, analysis),
                "html": build_html(profile, analysis, meta),
                "text": build_text(profile, analysis, meta),
                "attachments": attachments,
            }
        )
    except Exception as error:
        return {"sent": False, "error": f"{type(error).__name__}: {error}", "to": to}

    identifier = response.get("id") if isinstance(response, dict) else getattr(response, "id", "")
    return {"sent": True, "id": identifier or "", "to": to}
