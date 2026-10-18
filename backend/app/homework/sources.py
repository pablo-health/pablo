# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which fields of a note say what the client will do before the next visit.

Two kinds of field, read two ways:

* **A homework list** (``plan.homework_assignments`` on SOAP, DAP, BIRP and
  GIRP). Every item is already one assignment, written by the clinician or
  drafted and then signed by them. Each becomes one proposal, worded as the
  note words it with the leading subject taken off ("Client will complete a
  thought record" -> "Complete a thought record"). No model is involved.
* **Prose plan fields** (the psychiatric types). A goal-and-plan paragraph
  mixes medication changes, labs, follow-up timing and the odd between-visit
  task, so a sentence is not an assignment. These go to
  :mod:`app.homework.drafting`, which may only return tasks it can quote.

A practice's own type reads the fields of the built-in type it started from;
one built from scratch is read for a list field keyed ``homework_assignments``
and nothing else.

Pure and deterministic. Nothing here logs: every value is the note's text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..notes.registry import NoteTypeDefinition

HOMEWORK_LIST_FIELD = "homework_assignments"

FieldShape = Literal["list", "text"]


@dataclass(frozen=True)
class HomeworkSource:
    """One field read for homework, and how."""

    section: str
    field: str
    shape: FieldShape

    @property
    def key(self) -> str:
        return f"{self.section}.{self.field}"


_SESSION_LIST = (HomeworkSource("plan", HOMEWORK_LIST_FIELD, "list"),)

#: The built-in session types and the fields each is read for.
HOMEWORK_SOURCES: dict[str, tuple[HomeworkSource, ...]] = {
    "soap": _SESSION_LIST,
    "dap": _SESSION_LIST,
    "birp": _SESSION_LIST,
    "girp": _SESSION_LIST,
    # Medication management with a therapy component: the therapy plan, how
    # often therapy happens and what to practise in between, and the
    # lifestyle counselling, which is where "keep a sleep diary" ends up.
    "psychiatric_follow_up": (
        HomeworkSource("psychotherapy", "goal_plan", "text"),
        HomeworkSource("psychotherapy", "therapy_cadence", "text"),
        HomeworkSource("plan", "lifestyle_counseling", "text"),
    ),
    "psychiatric_evaluation": (
        HomeworkSource("psychotherapy", "goal_plan", "text"),
        HomeworkSource("treatment_plan", "plan_items", "text"),
        HomeworkSource("follow_up", "between_visits", "text"),
    ),
}


def sources_for(type_key: str | None) -> tuple[HomeworkSource, ...]:
    """The fields read for a built-in type key (a practice type passes its base)."""
    if type_key is None:
        return ()
    return HOMEWORK_SOURCES.get(type_key, ())


def sources_for_definition(definition: NoteTypeDefinition | None) -> tuple[HomeworkSource, ...]:
    """The fields read for a note type: a built-in's own, a practice type's base's,
    else any list field keyed ``homework_assignments`` the type has."""
    if definition is None:
        return ()
    if definition.key in HOMEWORK_SOURCES:
        return HOMEWORK_SOURCES[definition.key]
    if definition.based_on is not None and definition.based_on.key in HOMEWORK_SOURCES:
        return HOMEWORK_SOURCES[definition.based_on.key]
    return tuple(
        HomeworkSource(section.key, HOMEWORK_LIST_FIELD, "list")
        for section in definition.sections
        for f in section.fields
        if f.key == HOMEWORK_LIST_FIELD
    )


# A draft's way of saying a field was not covered. Matched against the whole
# item, so "None of the exercises were hard" is kept.
_PLACEHOLDER = re.compile(
    r"^(?:"
    r"(?:not|none|no\s+homework)(?:\s+(?:stated|reported|discussed|assigned|given|provided"
    r"|applicable|addressed|covered|noted|documented|indicated|identified))?"
    r"(?:\s+(?:this|at\s+this)\s+(?:visit|session|time))?"
    r"|n/?a|nothing\s+(?:assigned|discussed|noted)"
    r")[.!]?$",
    re.IGNORECASE,
)

# The subject a note puts in front of a task: "Client will", "The patient
# to", "Pt agreed to", "Client is to". Taken off so the item reads as the
# task itself, which is how the client sees it.
_SUBJECT_LEAD = re.compile(
    r"^\s*(?:the\s+)?(?:client|patient|pt\.?|member|individual)\s+"
    r"(?:will|to|is\s+to|agreed\s+to|agrees\s+to|plans\s+to|was\s+asked\s+to|"
    r"was\s+encouraged\s+to|is\s+encouraged\s+to|should)\s+",
    re.IGNORECASE,
)

#: Longest proposal kept. A list item past this is a paragraph, not a task.
MAX_TASK_CHARS = 300


def _flatten(value: Any) -> list[str]:
    """A field value as its non-empty text parts: str, ``{text}``, or a list of those."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return _flatten(value.get("text"))
    if isinstance(value, list):
        return [part for item in value for part in _flatten(item)]
    return []


def field_value(content: dict[str, Any] | None, source: HomeworkSource) -> Any:
    if not content:
        return None
    block = content.get(source.section)
    return block.get(source.field) if isinstance(block, dict) else None


def is_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER.match(text.strip()))


def as_task(item: str) -> str:
    """A homework item worded as the task: leading subject off, first letter up."""
    stripped = _SUBJECT_LEAD.sub("", item.strip(), count=1).strip()
    if not stripped:
        return item.strip()
    return stripped[:1].upper() + stripped[1:]


@dataclass(frozen=True)
class ListItem:
    """One item of a homework list: where it is, the note's words, and the task."""

    source_field: str
    item_index: int
    quote: str
    text: str


_NARRATIVE_LABEL = re.compile(r"\*\*([^*\n]+):\*\*")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def narrative_block(text: str, label: str) -> str | None:
    """What a formatted section string says under one field's label, ``None`` without it.

    A saved edit can keep a section as one string, each field under its bold
    label (``**Homework Assignments:**``) followed by bullets or text, up to
    the next label. This is how any note type's section reads that way, keyed
    by the type's own field label; it is not particular to one format.
    """
    labels = list(_NARRATIVE_LABEL.finditer(text))
    for at, found in enumerate(labels):
        if found.group(1).strip().lower() == label.strip().lower():
            end = labels[at + 1].start() if at + 1 < len(labels) else len(text)
            return text[found.end() : end].strip()
    return None


def narrative_items(text: str, label: str) -> list[str] | None:
    """A list field's items under its label in a section string (:func:`narrative_block`).

    A bullet starts an item and is stripped; a line with no bullet continues
    the item before it.
    """
    block = narrative_block(text, label)
    if block is None:
        return None
    items: list[str] = []
    for line in block.splitlines():
        if not line.strip():
            continue
        if _BULLET.match(line) or not items:
            items.append(_BULLET.sub("", line).strip())
        else:
            items[-1] = f"{items[-1]} {line.strip()}"
    return items


def list_items(
    content: dict[str, Any] | None, source: HomeworkSource, label: str | None = None
) -> list[ListItem]:
    """The homework list's items as proposals, placeholders and empties dropped.

    ``item_index`` is the item's position in the list as stored, so it stays
    the same however many placeholders sit before it. A section kept as one
    formatted string (a saved edit) is read under the field's ``label``.
    """
    value = field_value(content, source)
    block = (content or {}).get(source.section)
    if value is None and isinstance(block, str) and label:
        value = narrative_items(block, label)
    items = value if isinstance(value, list) else [value]
    out: list[ListItem] = []
    for index, item in enumerate(items):
        parts = _flatten(item)
        if not parts:
            continue
        quote = " ".join(parts)
        if is_placeholder(quote) or len(quote) > MAX_TASK_CHARS:
            continue
        out.append(ListItem(source.key, index, quote, as_task(quote)))
    return out


def text_of(
    content: dict[str, Any] | None, source: HomeworkSource, label: str | None = None
) -> str:
    """A prose field's text, one line per list item, or ``""`` when there is none.

    A section kept as one formatted string (a saved edit) is read under the
    field's ``label``, bullets and all.
    """
    value = field_value(content, source)
    block = (content or {}).get(source.section)
    if value is None and isinstance(block, str) and label:
        value = narrative_items(block, label)
    parts = [p for p in _flatten(value) if not is_placeholder(p)]
    return "\n".join(parts).strip()
