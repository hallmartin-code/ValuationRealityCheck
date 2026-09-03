"""TEN Capital Startup Valuation Analyzer.

    python -m app.main pitchdeck.pdf
    python -m app.main deck.pptx --inputs inputs/company.json --comparables inputs/comps.csv
    python -m app.main deck.pdf --output output/company_valuation.pdf

Deck -> Extract -> Normalize -> Select methods -> Value -> Adjust -> Validate ->
Interpret -> Render.

The run produces two files: the one-page PDF an investor reads, and the JSON
record holding every input, assumption, method and calculation behind it. When
Resend is configured, both are emailed to the desk inbox as well.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from . import config, llm, mailer, narrative
from .extraction import engine as extraction_engine
from .extraction import financing_extractor, profile_builder
from .ingestion import deck_reader
from .models.company import CompanyProfile
from .models.valuation import ValuationAnalysis
from .output import json_export
from .reporting import pdf_report
from .utils import formatting as fmt
from .utils import validation
from .valuation import valuation_engine

TOTAL_STEPS = 6

STEP_LABELS = (
    "Reading deck",
    "Extracting company, financing and operating data",
    "Normalising inputs and resolving source precedence",
    "Valuing the company",
    "Validating the analysis",
    "Writing the report",
)


class Progress:
    """Six-stage progress reporting. A full run waits on the model twice."""

    def __init__(self, sink: Any | None = None, echo: bool = True) -> None:
        self._sink = sink
        self._echo = echo
        self.step_number = 0

    def step(self, number: int, label: str) -> None:
        self.step_number = number
        if self._echo:
            print(f"[{number}/{TOTAL_STEPS}] {label}")
        self._emit("step", label)

    def detail(self, text: str, level: str = "info") -> None:
        if self._echo:
            marker = "      ! " if level == "flag" else "      "
            print(f"{marker}{text}")
        self._emit(level, text)

    def _emit(self, kind: str, text: str) -> None:
        if self._sink is not None:
            self._sink(self.step_number, kind, text)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        overrides = _load_inputs(args)
        comparables = financing_extractor.load_comparables(args.comparables)
        profile, analysis = run(
            Path(args.deck),
            overrides=overrides,
            comparables=comparables,
            analysis_date=date.fromisoformat(args.date) if args.date else None,
            interpret=not args.no_interpretation,
        )
    except llm.CredentialsError as error:
        print(f"\n{error}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(f"\nCould not complete the analysis: {error}", file=sys.stderr)
        return 1

    pdf_path, json_path = _paths(profile, args.output)

    Progress().step(6, STEP_LABELS[5])
    result = pdf_report.render(profile, analysis, pdf_path)
    record = json_export.build(profile, analysis)
    sidecar = json.dumps(record, indent=2, default=str)
    json_path.write_text(sidecar, encoding="utf-8")

    # Best-effort delivery: a Resend failure must not cost the analyst the
    # analysis they just waited on, so the outcome is reported, never raised.
    delivery = {"sent": False, "skipped": "disabled with --no-email"}
    if not args.no_email:
        delivery = mailer.send_analysis(
            profile,
            analysis,
            pdf_path.read_bytes(),
            sidecar.encode("utf-8"),
            stem=pdf_path.stem,
            meta={
                "source": Path(args.deck).name,
                "slides": profile.slide_count,
                "pages": result["pages"],
            },
        )

    _report(profile, analysis, result, json_path, delivery)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description="Build a TEN Capital startup valuation analysis from a pitch deck.",
    )
    parser.add_argument("deck", help="Path to the pitch deck (.pdf, .ppt or .pptx)")
    parser.add_argument(
        "--output", "-o", help="Output PDF path (default: output/<company>_valuation.pdf)"
    )
    parser.add_argument(
        "--inputs",
        "-i",
        help="JSON file of user-supplied values, which outrank the deck (see templates/).",
    )
    parser.add_argument(
        "--comparables",
        "-c",
        help=(
            "JSON or CSV file of comparable financings. Nothing is fetched or invented; "
            "without this the report states that comparable data was not supplied."
        ),
    )
    parser.add_argument("--company", help="Company name, overriding the deck.")
    parser.add_argument("--user-name", help="Name of the person requesting the analysis.")
    parser.add_argument("--user-email", help="Email of the person requesting the analysis.")
    parser.add_argument("--raise-amount", type=float, help="Round size, overriding the deck.")
    parser.add_argument(
        "--pre-money", type=float, help="Requested pre-money valuation, overriding the deck."
    )
    parser.add_argument(
        "--consent-analysis",
        action="store_true",
        help="Record that the user consented to this analysis. Never inferred.",
    )
    parser.add_argument(
        "--consent-storage",
        action="store_true",
        help="Record that the user consented to data storage. Never inferred.",
    )
    parser.add_argument(
        "--consent-contact",
        action="store_true",
        help="Record that the user consented to being contacted. Never inferred.",
    )
    parser.add_argument(
        "--no-interpretation",
        action="store_true",
        help=(
            "Skip the written interpretation pass. The deterministic decision factors and "
            "milestone are used instead; every number is unaffected."
        ),
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Do not email the result, even when Resend is configured.",
    )
    parser.add_argument("--date", help="Analysis date as YYYY-MM-DD (default: today)")
    return parser


def run(
    deck_path: Path,
    overrides: dict[str, Any] | None = None,
    comparables: list[Any] | None = None,
    analysis_date: date | None = None,
    interpret: bool = True,
    client: Any | None = None,
    progress: Progress | None = None,
) -> tuple[CompanyProfile, ValuationAnalysis]:
    """Run the full pipeline and return (profile, analysis)."""
    progress = progress or Progress()
    overrides = dict(overrides or {})

    progress.step(1, STEP_LABELS[0])
    deck = deck_reader.read_deck(deck_path)
    progress.detail(f"{deck['slide_count']} slides from {deck['source']}")

    progress.step(2, STEP_LABELS[1])
    facts, scorecard = extraction_engine.extract(deck, client=client)
    found = sum(
        1
        for group in extraction_engine.FIELDS
        for key in extraction_engine.FIELDS[group]
        if facts.present(group, key)
    )
    progress.detail(f"{found} fields found in the deck")
    for text in facts.contradictions:
        progress.detail(fmt.truncate_words(text, 24), "flag")

    progress.step(3, STEP_LABELS[2])
    profile = profile_builder.build(
        facts,
        scorecard,
        overrides=overrides,
        comparables=comparables,
        deck=deck,
        analysis_date=analysis_date,
    )
    unknown = profile_builder.unknown_keys(overrides)
    if unknown:
        progress.detail(f"unrecognised input keys ignored: {', '.join(unknown)}", "flag")
    progress.detail(
        f"{profile.identity.name} · {config.STAGE_LABELS[profile.stage]} · "
        f"{len(profile.ledger)} inputs "
        f"({len(profile.ledger.by_type(config.DECK))} deck, "
        f"{len(profile.ledger.by_type(config.USER))} user, "
        f"{len(profile.ledger.by_type(config.CALCULATED))} calculated)"
    )
    if not profile.usable_comparables:
        progress.detail(config.NO_COMPARABLES)

    progress.step(4, STEP_LABELS[3])
    analysis = valuation_engine.run(profile)
    for method in analysis.used_methods:
        progress.detail(
            f"{fmt.method_label(method.name)}: {fmt.fmt_money(method.base)} "
            f"at {fmt.fmt_pct(method.weight)} — {method.basis}"
        )
    progress.detail(
        f"range {fmt.fmt_range(analysis.range.low, analysis.range.high)}, base "
        f"{fmt.fmt_money(analysis.range.base)} "
        f"({fmt.fmt_signed_pct(analysis.net_adjustment)} net adjustment)"
    )

    progress.step(5, STEP_LABELS[4])
    analysis.issues = validation.run(profile, analysis)
    errors = len(analysis.errors())
    progress.detail(f"{len(analysis.issues)} findings ({errors} errors)")
    for issue in analysis.issues[:4]:
        progress.detail(
            f"[{issue.severity}] {fmt.truncate_words(issue.message, 20)}",
            "flag" if issue.severity == "ERROR" else "info",
        )

    if interpret:
        progress.detail("writing the interpretation")
        narrative.refine(profile, analysis, client=client, enabled=True)
    return profile, analysis


# --- Inputs -----------------------------------------------------------------


def _load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    """Merge the input file with the command-line overrides.

    The command line wins over the file: it is the more immediate statement of
    intent. Consent flags are only ever set by an explicit flag or an explicit
    `true` in the file — section 16 forbids inferring them.
    """
    overrides: dict[str, Any] = {}
    if args.inputs:
        path = Path(args.inputs)
        if not path.exists():
            raise FileNotFoundError(f"No input file at {path}")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{path.name}: expected a JSON object of field values.")
        overrides.update(loaded)

    for key, value in (
        ("company_name", args.company),
        ("user_name", args.user_name),
        ("user_email", args.user_email),
        ("raise_amount", args.raise_amount),
        ("requested_pre_money", args.pre_money),
    ):
        if value is not None:
            overrides[key] = value

    for key, flag in (
        ("analysis_consent", args.consent_analysis),
        ("data_storage_consent", args.consent_storage),
        ("contact_consent", args.consent_contact),
    ):
        if flag:
            overrides[key] = True
    return overrides


def _paths(profile: CompanyProfile, override: str | None) -> tuple[Path, Path]:
    """Resolve the PDF and JSON output paths, which always share a stem."""
    if override:
        pdf_path = Path(override)
        if pdf_path.suffix.lower() != ".pdf":
            pdf_path = pdf_path.with_suffix(".pdf")
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", profile.identity.name.lower()).strip("-") or "company"
        pdf_path = config.OUTPUT_DIR / f"{slug}_valuation.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    return pdf_path, pdf_path.with_suffix(".json")


def _report(
    profile: CompanyProfile,
    analysis: ValuationAnalysis,
    result: dict[str, Any],
    json_path: Path,
    delivery: dict[str, Any] | None = None,
) -> None:
    """Print the closing summary: where the files are, and what the analysis concluded."""
    ledger = profile.ledger
    print()
    print(f"  PDF   {result['path']}  ({result['pages']} page)")
    print(f"  JSON  {json_path}")
    print()
    print(
        f"  Pre-money  {fmt.fmt_money(analysis.range.low)} / "
        f"{fmt.fmt_money(analysis.range.base)} / {fmt.fmt_money(analysis.range.high)}"
    )
    if analysis.requested_valuation is not None:
        print(
            f"  Requested  {fmt.fmt_money(analysis.requested_valuation)} "
            f"({analysis.requested_basis}) — "
            f"{fmt.fmt_signed_pct(analysis.requested_gap_percent)} to base — "
            f"{analysis.requested_status}"
        )
    else:
        print(f"  Requested  {config.NOT_DISCLOSED}")
    print(
        f"  Provenance {len(ledger.by_type(config.USER))} user · "
        f"{len(ledger.by_type(config.DECK))} deck · "
        f"{len(ledger.by_type(config.COMPARABLE))} comparable · "
        f"{len(ledger.by_type(config.CALCULATED))} calculated · "
        f"{len(ledger.assumptions())} assumption"
    )
    print(f"  Confidence {analysis.confidence}")
    if delivery:
        if delivery.get("sent"):
            print(f"  Emailed to {', '.join(delivery.get('to', []))}")
        elif delivery.get("error"):
            print(f"  ! Email failed: {delivery['error']}")
        elif delivery.get("skipped"):
            print(f"  Email skipped: {delivery['skipped']}")
    errors = analysis.errors()
    if errors:
        print(f"  ! {len(errors)} validation error(s) — see the JSON record:")
        for issue in errors[:3]:
            print(f"      {issue.rule}: {fmt.truncate_words(issue.message, 20)}")
    if result["pages"] > 1:
        print("  ! Content exceeded one page at the tightest profile; review the PDF.")


if __name__ == "__main__":
    raise SystemExit(main())
