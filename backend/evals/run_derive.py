# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Run note-type derive on samples given by path, and score the result.

For notes that must never be committed: a clinician's own notes, or sample
notes shared privately. Nothing here copies an input anywhere but the
output directory; the console gets scores and counts, never sample text.

    scripts/run-derive-local.sh --samples a.pdf b.docx \\
        [--description "..."] [--reference-spec spec.json] \\
        [--transcript visit.txt] [--inputs inputs.json] [--out DIR] [--stand-in]

Writes to ``--out`` (a new temporary directory when omitted):

- ``proposal.json`` — the proposed note type;
- ``report.json`` / ``report.md`` — structure score against the reference
  spec (sections and fields matched by label, order agreement, missing and
  extra), the coverage report (sample passages no field took), and the
  copied-text check on the final proposal (expected empty);
- with ``--transcript``, ``drafts.json`` / ``drafts.md`` — the transcript
  drafted with the proposal and with the reference, side by side, through
  the same generation path a preview uses.

``--stand-in`` answers every model call with the end-to-end stack's
stand-in, so the runner itself can be tried without model access.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.models import Patient, Transcript
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.note_import_service import NoteImportService, extract_document_text
from app.services.note_type_derive_checks import SampleText, copied_paths, words
from app.services.note_type_derive_service import NoteTypeDeriveService
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.notes import NoteTypeDefinition
    from app.reliability import RetryPolicy
    from app.services.note_type_derive_service import DerivedNoteType

MATCH_THRESHOLD = 0.5
"""Least label similarity for two sections (or fields) to count as the same."""

_STEM = 5
"""Words are compared on their first letters, so "medication" meets "medications"."""

_STOP = frozenset({"and", "the", "of", "for", "with", "to", "in", "a", "an", "or", "on"})


# ---------------------------------------------------------------------------
# Structure score — pure, unit-tested
# ---------------------------------------------------------------------------


_MIN_PREFIX = 4
"""A word this long or longer also matches a word it begins ("exam", "examination")."""


def label_terms(text: str) -> frozenset[str]:
    return frozenset(w for w in words(text.replace("_", " ")) if w not in _STOP)


def _same_term(a: str, b: str) -> bool:
    if a[:_STEM] == b[:_STEM]:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= _MIN_PREFIX and long.startswith(short)


def similarity(a: str, b: str) -> float:
    """Overlap of two labels' terms, from 0 to 1 (Dice coefficient)."""
    left, right = label_terms(a), label_terms(b)
    if not left or not right:
        return 0.0
    hits = sum(any(_same_term(w, v) for v in right) for w in left)
    hits += sum(any(_same_term(v, w) for w in left) for v in right)
    return hits / (len(left) + len(right))


def match_labels(proposed: list[str], reference: list[str]) -> list[tuple[int, int, float]]:
    """Pair each reference label with at most one proposed label, best pairs first.

    Returns ``(proposed index, reference index, similarity)`` for pairs at
    or above :data:`MATCH_THRESHOLD`, sorted by reference index.
    """
    candidates = sorted(
        ((similarity(p, r), i, j) for i, p in enumerate(proposed) for j, r in enumerate(reference)),
        reverse=True,
    )
    used_p: set[int] = set()
    used_r: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for score, i, j in candidates:
        if score < MATCH_THRESHOLD or i in used_p or j in used_r:
            continue
        used_p.add(i)
        used_r.add(j)
        pairs.append((i, j, round(score, 2)))
    return sorted(pairs, key=lambda pair: pair[1])


def order_agreement(pairs: list[tuple[int, int, float]]) -> float:
    """Share of matched pairs that keep their relative order (1.0 = same order).

    The longest run of matched items in the same order on both sides, over
    the number matched.
    """
    sequence = [i for i, _, _ in sorted(pairs, key=lambda pair: pair[1])]
    if len(sequence) <= 1:
        return 1.0
    longest = [1] * len(sequence)
    for a in range(len(sequence)):
        for b in range(a):
            if sequence[b] < sequence[a]:
                longest[a] = max(longest[a], longest[b] + 1)
    return round(max(longest) / len(sequence), 2)


@dataclass
class StructureScore:
    section_recall: float
    section_precision: float
    section_order: float
    field_recall: float
    field_precision: float
    matched_sections: list[dict[str, Any]] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    extra_sections: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    extra_fields: list[str] = field(default_factory=list)


def _all_fields(spec: PracticeNoteTypeSpec) -> list[tuple[str, str]]:
    return [(s.label, f.label) for s in spec.sections for f in s.fields]


def score_structure(
    proposed: PracticeNoteTypeSpec, reference: PracticeNoteTypeSpec
) -> StructureScore:
    """How closely ``proposed`` reproduces ``reference``'s sections and fields.

    Sections match by label; fields match by label across the whole note,
    since a proposal may file a field under a differently cut section.
    """
    p_sections = [s.label for s in proposed.sections]
    r_sections = [s.label for s in reference.sections]
    pairs = match_labels(p_sections, r_sections)
    matched_p = {i for i, _, _ in pairs}
    matched_r = {j for _, j, _ in pairs}

    p_fields = _all_fields(proposed)
    r_fields = _all_fields(reference)
    field_pairs = match_labels([f for _, f in p_fields], [f for _, f in r_fields])
    fp = {i for i, _, _ in field_pairs}
    fr = {j for _, j, _ in field_pairs}

    def share(part: int, whole: int) -> float:
        return round(part / whole, 2) if whole else 1.0

    return StructureScore(
        section_recall=share(len(pairs), len(r_sections)),
        section_precision=share(len(pairs), len(p_sections)),
        section_order=order_agreement(pairs),
        field_recall=share(len(field_pairs), len(r_fields)),
        field_precision=share(len(field_pairs), len(p_fields)),
        matched_sections=[
            {"proposed": p_sections[i], "reference": r_sections[j], "similarity": s}
            for i, j, s in pairs
        ],
        missing_sections=[r for j, r in enumerate(r_sections) if j not in matched_r],
        extra_sections=[p for i, p in enumerate(p_sections) if i not in matched_p],
        missing_fields=[f"{s} / {f}" for j, (s, f) in enumerate(r_fields) if j not in fr],
        extra_fields=[f"{s} / {f}" for i, (s, f) in enumerate(p_fields) if i not in fp],
    )


def default_inputs(definition: NoteTypeDefinition, given: dict[str, str]) -> dict[str, str]:
    """The given inputs, plus a placeholder for each required one left out."""
    filled = dict(given)
    for item in definition.inputs:
        if item.required and not filled.get(item.key):
            filled[item.key] = item.options[0] if item.options else "Not provided"
    return filled


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def _read_sample(path: Path) -> str:
    return extract_document_text(path.read_bytes(), filename=path.name)


def _stand_in_gateway() -> StructuredLLMGateway:
    """The end-to-end stack's stand-in, called in process."""
    # The stand-in lives in the repository's scripts/, outside the backend.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    client = TestClient(importlib.import_module("scripts.fake_llm").app)

    class StandIn(StructuredLLMGateway):
        def complete_structured(
            self,
            *,
            model: str,
            system_prompt: str,
            user_prompt: str,
            response_schema: dict[str, Any],
            max_output_tokens: int,
            temperature: float = 0.3,
            thinking_budget: int | None = None,
            timeout_seconds: float | None = None,
            retry_policy: RetryPolicy | None = None,
        ) -> StructuredCompletion:
            reply = client.post(
                "/notes/v1/structured",
                # The whole call, as the HTTP gateway the stack uses sends it.
                json={
                    "model": model,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "response_schema": response_schema,
                    "max_output_tokens": max_output_tokens,
                    "temperature": temperature,
                    "thinking_budget": thinking_budget,
                    "timeout_seconds": timeout_seconds,
                    "retry_policy": None if retry_policy is None else repr(retry_policy),
                },
            )
            reply.raise_for_status()
            return StructuredCompletion(data=reply.json()["data"])

    return StandIn()


def _draft(
    gateway: StructuredLLMGateway | None,
    definition: NoteTypeDefinition,
    transcript: str,
    inputs: dict[str, str],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    # The preview's stand-in client: practice types never read the patient.
    patient = Patient(id="preview", first_name="", last_name="", created_at=now, updated_at=now)
    generator = RegistryNoteGenerationService(llm_gateway=gateway)
    generated = generator.generate_note(
        definition.key,
        Transcript(format="txt", content=transcript),
        patient,
        now,
        inputs=default_inputs(definition, inputs),
        definition=definition,
    )
    return generated.content


def _report_markdown(report: dict[str, Any]) -> str:
    lines = ["# Derive report", ""]
    structure = report.get("structure")
    if structure:
        lines += [
            "## Structure against the reference",
            "",
            f"- Sections: recall {structure['section_recall']}, precision "
            f"{structure['section_precision']}, order {structure['section_order']}",
            f"- Fields: recall {structure['field_recall']}, precision "
            f"{structure['field_precision']}",
            "",
            "Matched sections:",
            *(
                f"- {m['proposed']} = {m['reference']} ({m['similarity']})"
                for m in structure["matched_sections"]
            ),
            "",
            f"Missing sections: {', '.join(structure['missing_sections']) or 'none'}",
            f"Extra sections: {', '.join(structure['extra_sections']) or 'none'}",
            "",
            "Missing fields:",
            *(f"- {f}" for f in structure["missing_fields"]),
            "",
            "Extra fields:",
            *(f"- {f}" for f in structure["extra_fields"]),
            "",
        ]
    lines += ["## Coverage", ""]
    for c in report["coverage"]:
        lines.append(
            f"### Sample {c['sample']} ({c['file']}): {len(c['unplaced'])} of "
            f"{c['passages']} passages unplaced{'' if c['checked'] else ' (not checked)'}"
        )
        lines += [f"- {p}" for p in c["unplaced"]] + [""]
    lines += [
        "## Copied text",
        "",
        f"- In the final proposal (expected none): {report['copied_in_final'] or 'none'}",
        f"- Changed by the guard: {report['guard'] or 'none'}",
        "",
    ]
    return "\n".join(lines)


def _drafts_markdown(drafts: dict[str, dict[str, Any]]) -> str:
    lines = ["# Drafts, side by side", ""]
    for name, content in drafts.items():
        lines += [f"## {name}", ""]
        for section, fields in content.items():
            lines.append(f"### {section}")
            for key, value in (fields or {}).items():
                text = "; ".join(value) if isinstance(value, list) else str(value)
                lines.append(f"- **{key}**: {text}")
            lines.append("")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="derive-eval-"))
    out.mkdir(parents=True, exist_ok=True)
    gateway = _stand_in_gateway() if args.stand_in else None
    service = NoteTypeDeriveService(NoteImportService(llm_gateway=gateway), llm_gateway=gateway)

    paths = [Path(p) for p in args.samples or []]
    samples = [_read_sample(p) for p in paths]
    reference = (
        PracticeNoteTypeSpec.model_validate_json(Path(args.reference_spec).read_text())
        if args.reference_spec
        else None
    )
    derived: DerivedNoteType = service.derive(samples, args.description)

    copied = copied_paths(derived.spec, SampleText(samples)) if samples else []
    report: dict[str, Any] = {
        "structure": asdict(score_structure(derived.spec, reference)) if reference else None,
        "coverage": [
            {**asdict(c), "file": paths[c.sample].name if paths else ""} for c in derived.coverage
        ],
        "copied_in_final": copied,
        "guard": [asdict(g) for g in derived.guard],
        "repaired": derived.repaired,
    }
    (out / "proposal.json").write_text(derived.spec.model_dump_json(indent=2))
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.md").write_text(_report_markdown(report))

    if args.transcript:
        transcript = Path(args.transcript).read_text()
        inputs = json.loads(Path(args.inputs).read_text()) if args.inputs else {}
        proposed = to_definition("custom.proposed", 0, derived.spec)
        drafts = {"Proposed": _draft(gateway, proposed, transcript, inputs)}
        if reference is not None:
            drafts["Reference"] = _draft(
                gateway, to_definition("custom.reference", 0, reference), transcript, inputs
            )
        (out / "drafts.json").write_text(json.dumps(drafts, indent=2))
        (out / "drafts.md").write_text(_drafts_markdown(drafts))

    report["out"] = str(out)
    return report


def _summary(report: dict[str, Any]) -> str:
    """Scores and counts only: sample text stays in the output directory."""
    lines = [f"Output: {report['out']}"]
    s = report["structure"]
    if s:
        lines.append(
            f"Sections: recall {s['section_recall']} precision {s['section_precision']} "
            f"order {s['section_order']} | fields: recall {s['field_recall']} "
            f"precision {s['field_precision']} | missing sections {len(s['missing_sections'])}, "
            f"extra {len(s['extra_sections'])}"
        )
    for c in report["coverage"]:
        lines.append(
            f"Sample {c['sample']}: {len(c['unplaced'])}/{c['passages']} passages unplaced"
            + ("" if c["checked"] else " (not checked)")
        )
    lines.append(
        f"Copied text in final proposal: {len(report['copied_in_final'])} (expected 0); "
        f"guard changed {len(report['guard'])}; repaired {report['repaired']}"
    )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run note-type derive on local samples")
    p.add_argument("--samples", nargs="*", default=[], help="sample notes: PDF, Word or text")
    p.add_argument("--description", default=None, help="the notes described in plain words")
    p.add_argument("--reference-spec", default=None, help="a note type's JSON to score against")
    p.add_argument("--transcript", default=None, help="a transcript to draft with both")
    p.add_argument("--inputs", default=None, help="JSON of inputs for drafting")
    p.add_argument("--out", default=None, help="output directory (default: a new temp dir)")
    p.add_argument("--stand-in", action="store_true", help="use the e2e stand-in, not a model")
    args = p.parse_args(argv)
    if not args.samples and not args.description:
        p.error("give --samples or --description")
    return args


def main(argv: list[str] | None = None) -> int:
    print(_summary(run(_parse_args(argv))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
