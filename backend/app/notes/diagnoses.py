# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``diagnoses`` field kind: diagnoses as the clinician stated them.

A ``diagnoses`` field holds a list of items, each ``{label, code, status}``:
the diagnosis in the clinician's words, its code only when the clinician gave
one, and a status (working, rule out, ...) only when the clinician said so.
Keeping the parts apart, rather than one line of prose, is what lets a stated
diagnosis be carried onto the chart without re-reading a sentence.

The model records diagnoses; it never makes one. The prompt rule says so, and
nothing here fills a missing code or status.
"""

from __future__ import annotations

from typing import Any, TypedDict


class StatedDiagnosis(TypedDict):
    label: str
    code: str | None
    status: str | None


DIAGNOSIS_ITEM_TITLE = "StatedDiagnosis"
"""Names the item schema, so a stand-in model can tell these items from others."""

DIAGNOSES_KIND_LABEL = (
    "list of diagnoses, each an object with label, code and status; only diagnoses "
    "the clinician stated, the code only as the clinician said it, the status "
    '(e.g. "working", "rule out") only when the clinician said it, otherwise empty'
)
"""How a ``diagnoses`` field is described to the model in every field enumeration."""

DIAGNOSES_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "title": DIAGNOSIS_ITEM_TITLE,
        "properties": {
            "label": {"type": "string"},
            "code": {"type": "string"},
            "status": {"type": "string"},
        },
        "required": ["label"],
    },
}


def _part(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def coerce_diagnoses(raw: Any) -> list[StatedDiagnosis]:
    """Normalise a model reply (or an older plain-text list) into stated diagnoses.

    A bare string becomes a diagnosis with no code or status; it is never
    split, because guessing which part is a code would be inventing one.
    """
    if not isinstance(raw, list):
        return []
    out: list[StatedDiagnosis] = []
    for item in raw:
        if isinstance(item, dict):
            label = _part(item.get("label"))
            if label:
                out.append(
                    StatedDiagnosis(
                        label=label, code=_part(item.get("code")), status=_part(item.get("status"))
                    )
                )
        elif label := _part(item):
            out.append(StatedDiagnosis(label=label, code=None, status=None))
    return out


def diagnosis_text(item: Any) -> str:
    """One stated diagnosis as a line of text: ``label (code), status``."""
    if not isinstance(item, dict):
        return "" if item is None else str(item)
    text = str(item.get("label") or "").strip()
    if code := _part(item.get("code")):
        text = f"{text} ({code})"
    if status := _part(item.get("status")):
        text = f"{text}, {status}"
    return text
