"""Format dispatch and transcript assembly for a pitch deck.

`read_deck` is the only entry point the pipeline uses. It hands the file to the
right parser and flattens the result into a slide-labelled transcript — the form
the extraction prompt reads, and the reason a fact can be cited to a slide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import pdf_parser, ppt_parser

PDF = "pdf"
PPTX = "pptx"

_PARSERS = {PDF: pdf_parser.parse, PPTX: ppt_parser.parse}
_SUFFIXES = {".pdf": PDF, ".pptx": PPTX, ".ppt": PPTX}


def detect_kind(path: str | Path) -> str:
    """Return "pdf" or "pptx" from the file extension, or raise."""
    suffix = Path(path).suffix.lower()
    kind = _SUFFIXES.get(suffix)
    if kind is None:
        raise ValueError(
            f"Unsupported file type {suffix!r}: a pitch deck must be .pdf, .ppt or .pptx"
        )
    return kind


def read_deck(path: str | Path) -> dict[str, Any]:
    """Read a deck into {source, path, kind, slide_count, slides, tables, full_text, deck_date}."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No deck at {path}")

    parsed = _PARSERS[detect_kind(path)](path)
    slides = parsed["slides"]
    if not any(slide["text"] or slide["tables"] for slide in slides):
        raise ValueError(
            f"{path.name} yielded no extractable text. If the deck is a scan or an "
            "image-only export, re-export it with selectable text."
        )

    return {
        "source": path.name,
        "path": str(path),
        "kind": detect_kind(path),
        "slide_count": len(slides),
        "slides": slides,
        "tables": [
            {"slide": slide["index"], "rows": table}
            for slide in slides
            for table in slide["tables"]
        ],
        "full_text": render_transcript(slides),
        "deck_date": parsed.get("doc_date"),
    }


def render_transcript(slides: list[dict[str, Any]]) -> str:
    """Flatten slides into a slide-labelled transcript."""
    chunks: list[str] = []
    for slide in slides:
        header = f"--- Slide {slide['index']}"
        if slide["title"]:
            header += f": {slide['title']}"
        header += " ---"
        parts = [header]
        body = (slide["text"] or "").strip()
        if body:
            parts.append(body)
        for table in slide["tables"]:
            parts.append("[table]\n" + _table_to_text(table))
        chunks.append("\n".join(parts))
    return "\n\n".join(chunks)


def _table_to_text(rows: list[list[str]]) -> str:
    """Pipe-delimited rows, which models read more reliably than aligned columns."""
    return "\n".join(" | ".join(cell or "" for cell in row) for row in rows)
