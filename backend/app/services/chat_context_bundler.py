# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat context bundler — assembles the per-turn patient-context payload.

Pure function over the patient's chart. Each source type knows how to
load itself, serialize itself for LLM consumption, and emit a
PHI-free manifest entry. Adding a new source type is a closed-set
extension: implement the loader + serializer + manifest, then register
it in :data:`SOURCE_REGISTRY`.

Token budgeting: every source reports an estimate, and the assembler
enforces a deterministic priority order when the total exceeds
``token_budget``. ``pasted_text`` is always included — if it alone
exceeds the budget, ``ContextOverflowError`` is raised so the caller
can ask the user to trim before sending.

Manifest invariant: PHI never enters the manifest. Only ids, counts,
and token estimates. The assembled bundle text is PHI; the manifest
that describes it is not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..repositories.note import NotesRepository

logger = logging.getLogger(__name__)


class ContextOverflowError(Exception):
    """Raised when the bundle cannot fit in the token budget.

    Triggered when ``pasted_text`` alone exceeds the budget — every
    other source type is droppable per the priority order.
    """


@dataclass
class SourceResult:
    """Per-source output collected by the assembler."""

    source_key: str
    text: str
    tokens_est: int
    manifest: dict[str, Any]


@dataclass
class ContextBundle:
    """The fully-assembled patient context for a single chat turn.

    ``text`` is the serialized payload concatenated in the order
    sources appear in :data:`SOURCE_REGISTRY` (after dropping anything
    that didn't fit). ``manifest`` is PHI-free and persisted alongside
    the user message.
    """

    text: str
    total_tokens_est: int
    token_budget: int
    patient_id: str
    assembled_at: datetime
    sources_included: list[dict[str, Any]] = field(default_factory=list)
    sources_dropped: list[dict[str, Any]] = field(default_factory=list)

    def manifest(self) -> dict[str, Any]:
        return {
            "sources_included": self.sources_included,
            "sources_dropped": self.sources_dropped,
            "total_tokens_est": self.total_tokens_est,
            "token_budget": self.token_budget,
            "patient_id": self.patient_id,
            "assembled_at": self.assembled_at.isoformat(),
        }


# --------------------------------------------------------------------------- #
# Token estimation
# --------------------------------------------------------------------------- #


def estimate_tokens(text: str) -> int:
    """Cheap upper-bound token estimate (~4 chars/token).

    The real LLM gateway re-counts on the way out and reports actual
    usage; this estimate is only used to drive the budget heuristic.
    """
    return max(1, (len(text) + 3) // 4)


# --------------------------------------------------------------------------- #
# Source dependencies (loaders are injected to keep the bundler testable)
# --------------------------------------------------------------------------- #


@dataclass
class BundlerDeps:
    """Repository handles the source loaders need.

    Sources whose underlying data does not yet exist in the OSS schema
    (labs, vitals, medications, …) read empty results and emit empty
    manifest entries. They become real once those modules ship.
    """

    notes_repo: NotesRepository


@dataclass
class SourceDefinition:
    key: str
    priority: int  # lower = kept longer under budget pressure
    always_keep: bool
    load: Callable[
        [BundlerDeps, str, Any | None],
        SourceResult | None,
    ]


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #


_PASTED_HEADER = "USER-PASTED EXTERNAL DOCUMENT"
_NOTE_DIVIDER = "\n---\n"


def _serialize_note(content: dict[str, Any] | None) -> str:
    if not content:
        return ""
    parts: list[str] = []
    for section_key, section_value in content.items():
        if isinstance(section_value, dict):
            for field_key, field_value in section_value.items():
                if field_value is None:
                    continue
                parts.append(f"{section_key}.{field_key}: {field_value}")
        elif section_value is not None:
            parts.append(f"{section_key}: {section_value}")
    return "\n".join(parts)


def _format_note_block(note_id: str, note_type: str, body: str) -> str:
    return f"[note:{note_id} type={note_type}]\n{body}".rstrip()


# --------------------------------------------------------------------------- #
# Source loaders
# --------------------------------------------------------------------------- #


def _load_pasted_text(
    _deps: BundlerDeps, _patient_id: str, selection: Any | None
) -> SourceResult | None:
    if not selection:
        return None
    content = selection.get("content") if isinstance(selection, dict) else None
    if not content:
        return None
    text = f"{_PASTED_HEADER}\n{content}"
    return SourceResult(
        source_key="pasted_text",
        text=text,
        tokens_est=estimate_tokens(text),
        manifest={
            "source_key": "pasted_text",
            "char_count": len(content),
            "tokens_est": estimate_tokens(text),
        },
    )


def _load_progress_notes_recent(
    deps: BundlerDeps, patient_id: str, selection: Any | None
) -> SourceResult | None:
    limit = 3
    if isinstance(selection, dict):
        limit = max(1, int(selection.get("limit", 3)))
    elif selection is False:
        return None

    notes = deps.notes_repo.list_by_patient(patient_id)
    notes = [n for n in notes if (n.content_edited or n.content)][:limit]
    if not notes:
        return SourceResult(
            source_key="progress_notes_recent",
            text="",
            tokens_est=0,
            manifest={
                "source_key": "progress_notes_recent",
                "note_ids": [],
                "tokens_est": 0,
            },
        )

    blocks: list[str] = []
    note_ids: list[str] = []
    for note in notes:
        body = _serialize_note(note.content_edited or note.content)
        if not body:
            continue
        blocks.append(_format_note_block(note.id, note.note_type, body))
        note_ids.append(note.id)
    text = _NOTE_DIVIDER.join(blocks)
    return SourceResult(
        source_key="progress_notes_recent",
        text=text,
        tokens_est=estimate_tokens(text),
        manifest={
            "source_key": "progress_notes_recent",
            "note_ids": note_ids,
            "tokens_est": estimate_tokens(text),
        },
    )


def _load_progress_notes_explicit(
    deps: BundlerDeps, patient_id: str, selection: Any | None
) -> SourceResult | None:
    if not isinstance(selection, dict):
        return None
    note_ids = selection.get("note_ids") or []
    if not note_ids:
        return None

    by_id = {n.id: n for n in deps.notes_repo.list_by_patient(patient_id)}
    blocks: list[str] = []
    included_ids: list[str] = []
    for nid in note_ids:
        note = by_id.get(nid)
        if note is None:
            continue
        body = _serialize_note(note.content_edited or note.content)
        if not body:
            continue
        blocks.append(_format_note_block(note.id, note.note_type, body))
        included_ids.append(note.id)
    text = _NOTE_DIVIDER.join(blocks)
    return SourceResult(
        source_key="progress_notes_explicit",
        text=text,
        tokens_est=estimate_tokens(text),
        manifest={
            "source_key": "progress_notes_explicit",
            "note_ids": included_ids,
            "tokens_est": estimate_tokens(text),
        },
    )


def _load_unimplemented_source(key: str) -> Callable[
    [BundlerDeps, str, Any | None],
    SourceResult | None,
]:
    """Returns an empty-manifest loader for a source whose backing data
    does not yet exist in the OSS schema.

    The selection key is preserved so callers can keep their existing
    config; the manifest records ``status='unavailable'`` rather than
    silently omitting the source. Once labs/vitals/medications/etc.
    ship, the relevant loader is replaced in :data:`SOURCE_REGISTRY`.
    """

    def _load(_deps: BundlerDeps, _patient_id: str, selection: Any | None) -> SourceResult | None:
        if selection is None or selection is False:
            return None
        return SourceResult(
            source_key=key,
            text="",
            tokens_est=0,
            manifest={
                "source_key": key,
                "status": "unavailable",
                "tokens_est": 0,
            },
        )

    return _load


# --------------------------------------------------------------------------- #
# Source registry
# --------------------------------------------------------------------------- #


# Order is the deterministic priority order from §7.3 of the design.
# ``always_keep=True`` means the source is never dropped under budget
# pressure (truncation may still happen inside the source loader).
SOURCE_REGISTRY: list[SourceDefinition] = [
    SourceDefinition("pasted_text", priority=0, always_keep=True, load=_load_pasted_text),
    SourceDefinition(
        "current_medications",
        priority=1,
        always_keep=True,
        load=_load_unimplemented_source("current_medications"),
    ),
    SourceDefinition(
        "safety_plan_active",
        priority=2,
        always_keep=True,
        load=_load_unimplemented_source("safety_plan_active"),
    ),
    SourceDefinition(
        "most_recent_intake",
        priority=3,
        always_keep=False,
        load=_load_unimplemented_source("most_recent_intake"),
    ),
    SourceDefinition(
        "progress_notes_explicit",
        priority=4,
        always_keep=False,
        load=_load_progress_notes_explicit,
    ),
    SourceDefinition(
        "progress_notes_recent",
        priority=5,
        always_keep=False,
        load=_load_progress_notes_recent,
    ),
    SourceDefinition(
        "treatment_plan_active",
        priority=6,
        always_keep=False,
        load=_load_unimplemented_source("treatment_plan_active"),
    ),
    SourceDefinition(
        "lab_values_recent",
        priority=7,
        always_keep=False,
        load=_load_unimplemented_source("lab_values_recent"),
    ),
    SourceDefinition(
        "vitals_recent",
        priority=8,
        always_keep=False,
        load=_load_unimplemented_source("vitals_recent"),
    ),
]


DEFAULT_SOURCE_SELECTION: dict[str, Any] = {
    "current_medications": True,
    "most_recent_intake": True,
    "progress_notes_recent": {"limit": 3, "include_transcripts": False},
    "treatment_plan_active": True,
    "safety_plan_active": True,
    "lab_values_recent": {"limit": 5},
    "vitals_recent": {"limit": 5},
}


def assemble_context_bundle(
    *,
    deps: BundlerDeps,
    patient_id: str,
    selection: dict[str, Any] | None,
    token_budget: int,
) -> ContextBundle:
    """Build a bundle for one turn.

    ``selection`` is the per-message override; when None the caller
    should pass the conversation's default. ``token_budget`` is the
    upper bound the assembled bundle must respect after any drops.
    """
    selection = selection or {}
    results: list[SourceResult] = []

    for definition in SOURCE_REGISTRY:
        sel = selection.get(definition.key)
        if sel is None or sel is False:
            continue
        try:
            result = definition.load(deps, patient_id, sel)
        except Exception:
            logger.exception(
                "chat_context_bundler: source %s failed; skipping", definition.key
            )
            continue
        if result is None:
            continue
        results.append(result)

    # Pasted text alone exceeds the budget — caller must trim. We check
    # this before any other dropping happens because pasted text is
    # always-keep + non-truncatable.
    pasted = next((r for r in results if r.source_key == "pasted_text"), None)
    if pasted is not None and pasted.tokens_est > token_budget:
        raise ContextOverflowError(
            "Pasted document is too large for the chat context budget"
        )

    total = sum(r.tokens_est for r in results)
    dropped: list[dict[str, Any]] = []

    if total > token_budget:
        # Drop in reverse-priority order until we fit. always_keep sources
        # never get dropped — they may end up over budget, which we report
        # in the manifest so the UI can warn.
        droppable = sorted(
            [
                r for r in results
                if not next(
                    d.always_keep for d in SOURCE_REGISTRY if d.key == r.source_key
                )
            ],
            key=lambda r: -next(d.priority for d in SOURCE_REGISTRY if d.key == r.source_key),
        )
        for victim in droppable:
            if total <= token_budget:
                break
            results = [r for r in results if r.source_key != victim.source_key]
            dropped.append({"source_key": victim.source_key, "reason": "budget"})
            total -= victim.tokens_est

    # Re-sort by registry order so the serialized text reads consistently.
    order = {d.key: idx for idx, d in enumerate(SOURCE_REGISTRY)}
    results.sort(key=lambda r: order[r.source_key])

    text = "\n\n".join(r.text for r in results if r.text)
    return ContextBundle(
        text=text,
        total_tokens_est=total,
        token_budget=token_budget,
        patient_id=patient_id,
        assembled_at=utc_now(),
        sources_included=[r.manifest for r in results],
        sources_dropped=dropped,
    )
