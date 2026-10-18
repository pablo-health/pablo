# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework suggestions on a note, kept beside its other chart updates.

Made in the same step as the note's chart updates (``app.chart_proposals.step``)
and decided with them at signing:

* **After a draft or a redraft**, a psychiatric note's prose plan fields go to
  one quote-checked call (:mod:`.drafting`), holding no connection, beside the
  chart-update call. Its tasks replace the prose suggestions still pending.
* **Whenever the note's content changes** (a draft, a redraft, a saved edit),
  the suggestions are brought up to date with it. A homework list's items are
  read again as written, so an item the clinician edits, adds or deletes
  before signing is suggested as it now reads. A prose suggestion stays only
  while the words it quoted are still in the note; the call is not run again
  on an edit.

A suggestion the clinician accepted, edited or discarded is never replaced or
offered again on that note. Psychotherapy notes have no homework fields, so
they are never read.

Logs counts only.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from ..chart_proposals.families import HOMEWORK
from ..chart_proposals.models import ChartProposal, DraftedProposal
from ..utcnow import utc_now
from .drafting import HomeworkDrafter, PlanField
from .sources import list_items, sources_for_definition, text_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..models import Note
    from ..notes.registry import NoteTypeDefinition
    from ..repositories import ChartProposalRepository

logger = logging.getLogger(__name__)

_QUOTED = re.compile(r"^In the plan: “(?P<quote>.*)”$", re.DOTALL)
_SPACE = re.compile(r"\s+")


def what_changed(quote: str) -> str:
    """A suggestion's line under its label: the note's own words it came from."""
    return f"In the plan: “{quote}”"


def quote_of(line: str) -> str | None:
    """The quote :func:`what_changed` wrapped, or ``None``."""
    found = _QUOTED.match(line)
    return found.group("quote") if found else None


def _normalise(text: str) -> str:
    return _SPACE.sub(" ", text).strip().lower()


def proposes_homework(definition: NoteTypeDefinition | None) -> bool:
    """Whether notes of this type are read for homework."""
    return bool(sources_for_definition(definition))


def _suggestion(field: str, index: int, quote: str, text: str) -> DraftedProposal:
    return DraftedProposal(
        field_key=HOMEWORK,
        item_key=f"{field}#{index}",
        proposed_text=text,
        what_changed=what_changed(quote),
        evidence=(),
        origin="homework",
    )


def _labels(definition: NoteTypeDefinition | None) -> dict[str, str]:
    if definition is None:
        return {}
    return {f"{s.key}.{f.key}": f.label for s in definition.sections for f in s.fields}


def _prose_fields(
    definition: NoteTypeDefinition | None, content: Mapping[str, Any] | None
) -> list[PlanField]:
    labels = _labels(definition)
    fields: list[PlanField] = []
    for source in sources_for_definition(definition):
        if source.shape != "text":
            continue
        text = text_of(dict(content or {}), source, labels.get(source.key))
        if text:
            fields.append(PlanField(source.key, labels.get(source.key) or source.field, text))
    return fields


def draft_homework(
    definition: NoteTypeDefinition | None,
    content: Mapping[str, Any] | None,
    drafter: HomeworkDrafter,
) -> tuple[DraftedProposal, ...] | None:
    """The prose plan's tasks, after a draft. ``None`` when there was no call to
    make or it failed; the pending prose suggestions are then kept as they are."""
    fields = _prose_fields(definition, content)
    if not fields:
        return None
    try:
        tasks = drafter.draft(fields)
    except Exception:
        # The note stands; its list items and the clinician's own homework
        # are unaffected, and the next draft or redraft reads it again.
        logger.warning("Homework read failed for one note", exc_info=True)
        return None
    counts: dict[str, int] = {}
    drafted = []
    for task in tasks:
        index = counts.get(task.source_field, 0)
        counts[task.source_field] = index + 1
        drafted.append(_suggestion(task.source_field, index, task.quote, task.text))
    return tuple(drafted)


def _from_list(
    definition: NoteTypeDefinition | None, content: Mapping[str, Any] | None
) -> dict[str, DraftedProposal]:
    seeds: dict[str, DraftedProposal] = {}
    labels = _labels(definition)
    for source in sources_for_definition(definition):
        if source.shape != "list":
            continue
        for item in list_items(dict(content or {}), source, labels.get(source.key)):
            seed = _suggestion(item.source_field, item.item_index, item.quote, item.text)
            seeds[seed.item_key] = seed
    return seeds


def refresh_homework(
    proposals: ChartProposalRepository,
    note: Note,
    definition: NoteTypeDefinition | None,
    content: Mapping[str, Any] | None,
    drafted: tuple[DraftedProposal, ...] | None = None,
) -> None:
    """Bring the note's homework suggestions up to date with ``content``.

    ``drafted`` is the prose read's answer after a draft or redraft, replacing
    the prose suggestions still pending; ``None`` keeps those whose quote the
    note still contains. List suggestions are recomputed every time.
    """
    if not proposes_homework(definition):
        return
    existing = [p for p in proposals.list_for_note(note.id) if p.field_key == HOMEWORK]
    decided = {p.item_key for p in existing if not p.pending}
    pending = {p.item_key: p for p in existing if p.pending}
    seeds = _from_list(definition, content)
    prose = {f.key: _normalise(f.text) for f in _prose_fields(definition, content)}

    def still_quoted(p: ChartProposal) -> bool:
        field = p.item_key.split("#", 1)[0]
        quote = quote_of(p.what_changed)
        return field in prose and quote is not None and _normalise(quote) in prose[field]

    list_fields = {s.key for s in sources_for_definition(definition) if s.shape == "list"}
    stale: list[ChartProposal] = []
    for key, p in pending.items():
        if key.split("#", 1)[0] in list_fields:
            # A list item as it reads now: gone or reworded, it is suggested afresh.
            seed = seeds.get(key)
            if seed is None or seed.proposed_text != p.proposed_text:
                stale.append(p)
        elif drafted is not None or not still_quoted(p):
            stale.append(p)
    proposals.delete(p.id for p in stale)
    taken = decided | {k for k, p in pending.items() if p not in stale}

    now = utc_now()
    wanted = [*seeds.values(), *(drafted or ())]
    added = []
    for d in wanted:
        if d.item_key in taken:
            continue
        taken.add(d.item_key)
        added.append(
            ChartProposal(
                id=str(uuid.uuid4()),
                note_id=note.id,
                patient_id=note.patient_id,
                field_key=HOMEWORK,
                item_key=d.item_key,
                proposed_text=d.proposed_text,
                what_changed=d.what_changed,
                evidence=(),
                origin="homework",
                created_at=now,
            )
        )
    proposals.add(added)
    if added or stale:
        logger.info("Homework suggestions: %d added, %d dropped", len(added), len(stale))
