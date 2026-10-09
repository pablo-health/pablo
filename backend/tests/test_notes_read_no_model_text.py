# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""No regular expression reads what a model wrote.

What a draft says about a time, a count or a choice comes back as a field
of a structured reply and is compared field by field. A pattern over the
model's prose agrees with the prose its author pictured and fails quietly
on the next phrasing. The regular expressions left in ``app/notes`` parse
transcripts and templates or split names into words, with one exception
named below that still reads codes out of a draft. Each is named here with
what it reads; a new one fails until it is.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.notes
from app.services import therapy_labels

NOTES = Path(app.notes.__file__).parent

NAMED_PATTERNS = {
    "client_present.py": "transcript lines as stored",
    # The clinician's dictated codes and times as the transcriber wrote them;
    # a drafted diagnosis code is compared as a string, never by a pattern.
    "dictated_numbers.py": "numbers in the transcript's dictated lines",
    # Reads the E/M and add-on codes out of the drafted visit details, which a
    # model writes: the one place left that reads a value out of model prose.
    # Named so it stays visible until the draft returns the codes as fields.
    "mdm_review.py": "codes in the drafted visit details (model text; to be structured)",
    "practice_spec.py": "placeholders in a template a practice wrote",
    "practice_types.py": "placeholders in a template a practice wrote",
    # A derived type's section names may come from a model; they are split into
    # words to match a reference's terms, and no value is read out of them.
    "references.py": "words of section names, for matching",
    "transcript_normalize.py": "transcripts from a transcription provider or an upload",
}


def _uses_re(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        (isinstance(node, ast.Import) and any(a.name == "re" for a in node.names))
        or (isinstance(node, ast.ImportFrom) and node.module == "re")
        for node in ast.walk(tree)
    )


def test_only_the_named_modules_use_a_regular_expression() -> None:
    using = {str(p.relative_to(NOTES)) for p in NOTES.rglob("*.py") if _uses_re(p)}
    assert using <= set(NAMED_PATTERNS), (
        f"{sorted(using - set(NAMED_PATTERNS))} use a regular expression; "
        "read a structured reply instead, or name what the pattern reads here"
    )


def test_the_psychotherapy_time_is_read_without_one() -> None:
    assert not _uses_re(NOTES / "visit_times.py")
    assert therapy_labels.__file__ is not None
    assert not _uses_re(Path(therapy_labels.__file__))
