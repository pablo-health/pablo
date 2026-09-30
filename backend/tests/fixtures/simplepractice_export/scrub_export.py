"""Scrub a records-system export in place so it can be committed as a fixture.

The PDFs stay the files the other system produced: each span whose text matches
an entry in the mapping is redacted where it sits and the replacement written
back at the same origin and size, and each note body named in the mapping is
redacted as a block and re-set in the same box. Producer, fonts, header layout,
columns, tables and footers are untouched. vCards and plain-text files get the
same text replacements; files under ``Stored documents`` are represented by
stand-ins only when the mapping says so, never by their original bytes.

Usage::

    python scrub_export.py --map mapping.json <export dir> <fixture dir>

The mapping file is kept OUTSIDE this repository, because it pairs the real
values with their replacements. Its shape::

    {
      "text": [["Old Name", "New Name"], ["05/26/1982", "03/14/1990"]],
      "left_column_email": {"old@x.test": ["client@x.test", "provider@x.test"]},
      "ip_replacement": "203.0.113.10",
      "blank_spans": ["condition"],
      "bodies": {"Progress Note 2026-09-24 130000 1007836363.pdf": "Rewritten body"},
      "path_renames": [["Old Name", "New Name"]],
      "stand_ins": {"Stored documents/C/1-real.pdf": "Stored documents/C/1-Sample.txt"}
    }

``text`` entries are applied longest key first. ``left_column_email`` handles a
value that means the client on the left half of a billing page and the
provider on the right half. ``blank_spans`` removes a span that is exactly
that text (a wrapped continuation line left behind by a shorter replacement).
Run the script, then extract the result and grep for every real value before
committing; the script prints the files it wrote and nothing else.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

TITLES = {"Progress Note", "Psychotherapy Note", "Chart Note", "Administrative Note"}
BODY_END_PREFIXES = ("Created on", "Signed by")
IP_RE = re.compile(r"IP address: \d+\.\d+\.\d+\.\d+")
BODY_BOX_LINES = 8
LINE_SPACING = 1.35
RIGHT_MARGIN = 36


@dataclass(frozen=True)
class SpanEdit:
    rect: pymupdf.Rect
    origin: tuple[float, float]
    size: float
    font: str
    text: str


def load_mapping(path: Path) -> dict:
    mapping: dict = json.loads(path.read_text())
    mapping["text"] = sorted(mapping.get("text", []), key=lambda kv: -len(kv[0]))
    for key, default in (
        ("left_column_email", {}),
        ("blank_spans", []),
        ("bodies", {}),
        ("path_renames", []),
        ("stand_ins", {}),
    ):
        mapping.setdefault(key, default)
    return mapping


def replace_text(text: str, mapping: dict, *, left_half: bool | None = None) -> str:
    new = text
    for old, (left, right) in mapping["left_column_email"].items():
        if old in new:
            new = new.replace(old, left if left_half else right)
    for old, rep in mapping["text"]:
        new = new.replace(old, rep)
    if mapping.get("ip_replacement"):
        new = IP_RE.sub(f"IP address: {mapping['ip_replacement']}", new)
    if new.strip() in mapping["blank_spans"]:
        new = ""
    return new


def font_for(span: dict) -> str:
    return "hebo" if "bold" in span["font"].lower() else "helv"


def collect_edits(
    page: pymupdf.Page, mapping: dict, rewrite_body: bool
) -> tuple[list[SpanEdit], list[dict]]:
    """Walk the page once: span edits for identifiers, and the body's spans if asked."""
    edits: list[SpanEdit] = []
    body_spans: list[dict] = []
    in_body = False
    mid = page.rect.width / 2
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            line_text = "".join(s["text"] for s in spans).strip()
            if rewrite_body and not in_body and line_text in TITLES:
                in_body = True
                continue
            if in_body and (line_text == "Provider" or line_text.startswith(BODY_END_PREFIXES)):
                in_body = False
            if in_body and line_text:
                body_spans.extend(spans)
                continue
            for s in spans:
                new = replace_text(s["text"], mapping, left_half=s["bbox"][0] < mid)
                if new != s["text"]:
                    edits.append(
                        SpanEdit(pymupdf.Rect(s["bbox"]), s["origin"], s["size"], font_for(s), new)
                    )
    return edits, body_spans


def apply_edits(
    page: pymupdf.Page, edits: list[SpanEdit], body_spans: list[dict], body: str | None
) -> None:
    for e in edits:
        page.add_redact_annot(e.rect)
    body_rect: pymupdf.Rect | None = None
    if body_spans:
        body_rect = pymupdf.Rect(body_spans[0]["bbox"])
        for s in body_spans[1:]:
            body_rect |= pymupdf.Rect(s["bbox"])
        page.add_redact_annot(body_rect)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    for e in edits:
        if e.text:
            page.insert_text(pymupdf.Point(e.origin), e.text, fontsize=e.size, fontname=e.font)
    if body_rect is not None and body is not None:
        size = body_spans[0]["size"]
        top = body_rect.y0 - 1
        box = pymupdf.Rect(
            body_rect.x0,
            top,
            page.rect.width - RIGHT_MARGIN,
            top + size * LINE_SPACING * BODY_BOX_LINES,
        )
        page.insert_textbox(
            box, body, fontsize=size, fontname="helv", align=pymupdf.TEXT_ALIGN_LEFT
        )


def scrub_pdf(src: Path, dst: Path, mapping: dict) -> None:
    doc = pymupdf.open(src)
    body = mapping["bodies"].get(src.name)
    for page in doc:
        edits, body_spans = collect_edits(page, mapping, rewrite_body=body is not None)
        apply_edits(page, edits, body_spans, body)
    dst.parent.mkdir(parents=True, exist_ok=True)
    doc.save(dst, garbage=4, deflate=True)
    doc.close()


def rename_path(rel: Path, mapping: dict) -> Path:
    s = str(rel)
    for old, new in mapping["path_renames"]:
        s = s.replace(old, new)
    return Path(s)


def scrub_one(src: Path, rel: Path, fixture_dir: Path, mapping: dict) -> Path | None:
    if rel.parts[0] == "Stored documents":
        stand_in: str | None = mapping["stand_ins"].get(str(rel))
        if not stand_in:
            return None
        dst = fixture_dir / str(stand_in)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            dst.write_text(
                "Stand-in for an opaque client upload; only the path shape is captured.\n"
            )
        return dst
    dst = fixture_dir / rename_path(rel, mapping)
    if src.suffix.lower() == ".pdf":
        scrub_pdf(src, dst, mapping)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # A vCard or text file has no columns: an email there belongs to the client.
        dst.write_text(replace_text(src.read_text(), mapping, left_half=True))
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrub a records-system export into a fixture.")
    ap.add_argument("--map", required=True, type=Path)
    ap.add_argument("export_dir", type=Path)
    ap.add_argument("fixture_dir", type=Path)
    args = ap.parse_args()
    mapping = load_mapping(args.map)
    sources = sorted(p for p in args.export_dir.rglob("*") if p.is_file() and p.name != ".DS_Store")
    for src in sources:
        dst = scrub_one(src, src.relative_to(args.export_dir), args.fixture_dir, mapping)
        if dst is not None:
            print(dst.relative_to(args.fixture_dir))


if __name__ == "__main__":
    main()
