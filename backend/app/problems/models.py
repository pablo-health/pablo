# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Problem-list domain model, code format, and the derived diagnosis line."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime


class ProblemStatus(StrEnum):
    ACTIVE = "active"
    RULE_OUT = "rule_out"
    RESOLVED = "resolved"


#: List order: what the client has, then what is being ruled out, then history.
STATUS_ORDER: dict[str, int] = {
    ProblemStatus.ACTIVE: 0,
    ProblemStatus.RULE_OUT: 1,
    ProblemStatus.RESOLVED: 2,
}

# ICD-10-CM shape: a letter, two characters, then an optional dot and up to
# four more. Shape only — a code the bundled catalog has never heard of is
# still a code a clinician may need to record.
_ICD10_CODE = re.compile(r"^[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")
_ICD10_IN_TEXT = re.compile(r"\b[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b")
# Separators around a code: punctuation, plus the en and em dash.
_LABEL_TRIM = " \t-:;,()[]" + chr(0x2013) + chr(0x2014)


@dataclass
class Problem:
    id: str
    patient_id: str
    label: str
    icd10_code: str | None
    status: str
    position: int
    added_at: datetime
    updated_at: datetime
    onset_date: date | None = None
    source_note_id: str | None = None
    added_by: str | None = None
    resolved_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def display(self) -> str:
        return f"{self.label} ({self.icd10_code})" if self.icd10_code else self.label


def normalize_icd10_code(value: str | None) -> str | None:
    """Tidy a typed code and check its shape; blank clears it.

    Raises ``ValueError`` for something that is not shaped like an ICD-10-CM
    code, so a label typed into the code field is caught at entry.
    """
    if value is None:
        return None
    code = value.strip().upper()
    if not code:
        return None
    if not _ICD10_CODE.match(code):
        raise ValueError(f"{value!r} is not an ICD-10-CM code (for example F41.1)")
    return code


def split_free_text_diagnosis(text: str) -> tuple[str, str | None]:
    """Read a free-text diagnosis as ``(label, code)``.

    The code is taken only when the text names exactly one; with none or
    several it stays ``None`` and the whole text is the label, since picking
    one of several would be a guess.
    """
    cleaned = " ".join(text.split())
    codes = _ICD10_IN_TEXT.findall(cleaned)
    if len(codes) != 1:
        return cleaned, None
    code = codes[0]
    label = _ICD10_IN_TEXT.sub(" ", cleaned)
    label = " ".join(label.split()).strip(_LABEL_TRIM)
    return (label or cleaned), code


def sort_problems(problems: Iterable[Problem]) -> list[Problem]:
    return sorted(problems, key=lambda p: (STATUS_ORDER.get(p.status, 3), p.position))


def derived_diagnosis(problems: Iterable[Problem]) -> str | None:
    """The active problems as one display line, or ``None`` when there are none."""
    active = [
        p
        for p in sort_problems(problems)
        if p.status == ProblemStatus.ACTIVE and p.deleted_at is None
    ]
    return "; ".join(p.display for p in active) or None
