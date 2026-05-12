# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Context bundle assembler for patient-context chat (THERAPY-r3c, Phase 2 of THERAPY-bhv).

Pure function over a patient's chart. Given a typed ``SourceSelection``
the assembler:

1. Loads each requested source from the supplied :class:`NotesRepository`
   (and future repositories for labs / vitals once those modules ship).
2. Serializes each source as a bounded text section suitable for
   inclusion in an LLM prompt envelope.
3. Estimates token usage with a deterministic char-based heuristic
   (Gemini tokenizes ~3.5-4 chars/token for English clinical text).
4. Enforces a token budget by walking sources in reverse priority order
   and either truncating or dropping them, matching the priority list
   in ``docs/architecture/patient-context-chat-oss.md`` §7.3.
5. Produces a PHI-free :class:`ContextManifest` for persistence on the
   user-turn ``chat_messages`` row and for the audit log digest.

The bundler is intentionally repository-driven so that the same code
runs against the in-memory test repo and the Postgres repo. The
Phase-3 streaming turn service composes the bundle with the caller's
system prompt + prior turns; bundling itself is stateless.

Note types referenced by source loaders (``intake``, ``treatment_plan``,
``safety_plan``, ``medications``) are not yet registered in the OSS
note-type registry — only ``soap`` and ``narrative`` ship today. The
loaders match against those keys defensively so that when a follow-up
PR registers them, the bundler picks them up without code changes. In
the meantime the manifest correctly reports ``row_count=0`` with
``reason="no_data"`` for any selected-but-empty source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..utcnow import utc_now_iso

if TYPE_CHECKING:
    from ..models import Note
    from ..repositories import NotesRepository


# ---------------------------------------------------------------------------
# Source keys + note-type bindings
# ---------------------------------------------------------------------------

# Source keys exposed to callers. Frozen tuple so it's hashable and
# stable across imports — the SaaS overlay imports this set for tier
# gating in ``caller_feature_key`` enforcement.
SOURCE_KEY_PASTED_TEXT = "pasted_text"
SOURCE_KEY_CURRENT_MEDICATIONS = "current_medications"
SOURCE_KEY_MOST_RECENT_INTAKE = "most_recent_intake"
SOURCE_KEY_PROGRESS_NOTES_RECENT = "progress_notes_recent"
SOURCE_KEY_PROGRESS_NOTES_EXPLICIT = "progress_notes_explicit"
SOURCE_KEY_TREATMENT_PLAN_ACTIVE = "treatment_plan_active"
SOURCE_KEY_SAFETY_PLAN_ACTIVE = "safety_plan_active"
SOURCE_KEY_LAB_VALUES_RECENT = "lab_values_recent"
SOURCE_KEY_VITALS_RECENT = "vitals_recent"

V1_SOURCE_KEYS: tuple[str, ...] = (
    SOURCE_KEY_PASTED_TEXT,
    SOURCE_KEY_CURRENT_MEDICATIONS,
    SOURCE_KEY_MOST_RECENT_INTAKE,
    SOURCE_KEY_PROGRESS_NOTES_RECENT,
    SOURCE_KEY_PROGRESS_NOTES_EXPLICIT,
    SOURCE_KEY_TREATMENT_PLAN_ACTIVE,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE,
    SOURCE_KEY_LAB_VALUES_RECENT,
    SOURCE_KEY_VITALS_RECENT,
)

# Note-type keys this assembler recognizes when filtering Notes by type.
# Session-context notes feed ``progress_notes_*``; patient-context notes
# feed the named patient-document sources. Future additions land here.
SESSION_NOTE_TYPES: frozenset[str] = frozenset({"soap", "narrative"})
INTAKE_NOTE_TYPES: frozenset[str] = frozenset({"intake", "biopsychosocial"})
TREATMENT_PLAN_NOTE_TYPES: frozenset[str] = frozenset({"treatment_plan"})
SAFETY_PLAN_NOTE_TYPES: frozenset[str] = frozenset({"safety_plan", "stanley_brown"})
MEDICATIONS_NOTE_TYPES: frozenset[str] = frozenset({"medications", "medication_list"})


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Default token budget. Well below Gemini's 1M window — leaves headroom
# for the caller system prompt, prior turns, and the response. See
# design doc §7.3.
DEFAULT_TOKEN_BUDGET = 600_000

# Cap on a single pasted-text source per the design doc §7.2.
PASTED_TEXT_MAX_CHARS = 32_000

# Upper bound on ``progress_notes_recent.limit`` — guards against a
# caller asking for an unbounded number of notes.
PROGRESS_NOTES_LIMIT_MAX = 50

# Bytes-per-token heuristic. Gemini tokenizers run roughly 3.5-4 chars
# per token on English clinical prose; we use 4 as a slight
# under-estimate of budget headroom (i.e. the assembler treats a chunk
# of text as costing fewer tokens than it actually does only on the
# margin, and the Phase-3 turn service re-checks the real count from
# the API response).
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count from a text section.

    Char-based heuristic. The Phase-3 turn service overrides this with
    the LLM gateway's real token count once a call has been made; this
    estimator only needs to be good enough for budget enforcement
    inside the assembler.
    """
    if not text:
        return 0
    # Round up so that a single non-empty token survives the divide.
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ContextOverflowError(Exception):
    """Raised when the pasted-text source alone exceeds the budget.

    Design doc §7.3: ``pasted_text`` is never truncated. If the user
    pasted a document that won't fit even with every other source
    dropped, the bundler refuses to assemble and the route surfaces the
    error to the UI. Any other budget overflow is handled silently by
    dropping/truncating lower-priority sources.
    """

    def __init__(self, pasted_tokens: int, token_budget: int) -> None:
        super().__init__(
            f"pasted_text source ({pasted_tokens} est. tokens) exceeds "
            f"token_budget ({token_budget}); trim the pasted document"
        )
        self.pasted_tokens = pasted_tokens
        self.token_budget = token_budget


class InvalidSelectionError(ValueError):
    """Raised when the caller-supplied selection is structurally invalid.

    Examples: unknown source key, malformed sub-dict shape, pasted text
    over the per-paste character cap. The route layer turns this into a
    422; the bundler treats it as a programming error.
    """


# ---------------------------------------------------------------------------
# Loaded source state
# ---------------------------------------------------------------------------


@dataclass
class LoadedSource:
    """Mutable per-source state while the assembler walks the budget.

    A loader populates this once at load time. The truncation loop
    mutates ``rows`` and re-derives ``text`` + ``tokens_est`` so the
    final state reflects exactly what the LLM sees.
    """

    key: str
    priority: int  # 1 = highest priority (kept first); see §7.3
    rows: list[Any]
    extra: dict[str, Any]  # loader-specific scratch (e.g. note ids, drop counts)
    text: str
    tokens_est: int
    truncatable: bool
    dropped_reason: str | None = None

    @property
    def is_present(self) -> bool:
        return self.dropped_reason is None

    def render_section(self) -> str | None:
        """Return the section text or ``None`` if the source was dropped."""
        if not self.is_present:
            return None
        if not self.text:
            return None
        return self.text


# ---------------------------------------------------------------------------
# Final bundle + manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextBundle:
    """Frozen result of assembling context for a single turn."""

    text: str
    """Concatenated context block ready to splice into the LLM prompt
    envelope. Each section is wrapped in a header for the model. Empty
    string when no source had content."""

    manifest: dict[str, Any]
    """PHI-free manifest persisted on the user turn (``chat_messages``)
    and digest-hashed for the purge audit row."""

    total_tokens_est: int
    """Estimated tokens consumed by ``text``."""


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------


def _filter_notes_by_type(notes: list[Note], note_types: frozenset[str]) -> list[Note]:
    return [n for n in notes if n.note_type in note_types]


def _note_display_text(note: Note) -> str:
    """Best-effort textual rendering of a Note's content.

    Prefers ``content_edited`` (the clinician's authoritative version),
    falls back to ``content``. For ``soap`` notes we emit the narrative
    derived from the structured form; for everything else we walk the
    JSON tree and stringify leaves.
    """
    body = note.content_edited or note.content
    if not body:
        return ""

    if note.note_type == "soap":
        from ..models.soap_note import SOAPNote  # local import — soap_note is heavy

        try:
            soap = SOAPNote.from_dict(body)
        except (KeyError, TypeError, ValueError):
            return _flatten_json(body)
        narrative = soap.to_narrative()
        sections = []
        for label in ("Subjective", "Objective", "Assessment", "Plan"):
            chunk = narrative.get(label.lower(), "").strip()
            if chunk:
                sections.append(f"{label}:\n{chunk}")
        return "\n\n".join(sections)

    return _flatten_json(body)


def _flatten_json(value: Any, *, prefix: str = "") -> str:
    """Render an arbitrary JSON blob as readable text.

    Used as the generic fallback for note types whose structure the
    bundler doesn't know about — e.g. intake/treatment_plan once
    registered, or any custom extension type a downstream overlay adds.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            rendered = _flatten_json(item, prefix=prefix)
            if rendered:
                parts.append(f"- {rendered}")
        return "\n".join(parts)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            rendered = _flatten_json(v, prefix=prefix + "  ")
            if not rendered:
                continue
            label = str(k).replace("_", " ").strip().title()
            if "\n" in rendered:
                parts.append(f"{label}:\n{rendered}")
            else:
                parts.append(f"{label}: {rendered}")
        return "\n".join(parts)
    return str(value)


def _format_note_section(note: Note, header: str) -> str:
    """Wrap a note's display text with a dated header line."""
    when = (note.finalized_at or note.updated_at or note.created_at).strftime("%Y-%m-%d")
    body = _note_display_text(note)
    if not body.strip():
        return ""
    return f"### {header} — {when}\n{body.strip()}"


def _load_pasted_text(raw: Any) -> LoadedSource:
    if not isinstance(raw, dict) or "content" not in raw:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PASTED_TEXT} selection must be {{'content': str}}"
        )
    content = raw["content"]
    if not isinstance(content, str):
        raise InvalidSelectionError(f"{SOURCE_KEY_PASTED_TEXT}.content must be a string")
    if len(content) > PASTED_TEXT_MAX_CHARS:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PASTED_TEXT} exceeds {PASTED_TEXT_MAX_CHARS} characters"
        )
    text = (
        "## USER-PASTED EXTERNAL DOCUMENT\n"
        "The clinician supplied the following text outside the chart. "
        "Treat it as caller-provided context, not a chart artifact.\n\n"
        f"{content.strip()}"
    )
    return LoadedSource(
        key=SOURCE_KEY_PASTED_TEXT,
        priority=1,
        rows=[content],
        extra={"chars": len(content)},
        text=text,
        tokens_est=estimate_tokens(text),
        truncatable=False,
    )


def _load_notes_source(
    *,
    notes: list[Note],
    note_types: frozenset[str],
    key: str,
    priority: int,
    header: str,
    limit: int | None,
    truncatable: bool,
    section_prefix: str,
) -> LoadedSource:
    """Generic note-backed loader used by the patient-document sources.

    ``notes`` is the already-loaded patient note list (sorted newest
    first by ``NotesRepository.list_by_patient``). The loader filters
    by type, optionally caps to the most-recent N, and renders a section
    block.
    """
    matched = _filter_notes_by_type(notes, note_types)
    if limit is not None:
        matched = matched[:limit]
    rendered = [_format_note_section(n, header) for n in matched]
    rendered = [r for r in rendered if r]
    text = f"## {section_prefix}\n\n" + "\n\n".join(rendered) if rendered else ""
    return LoadedSource(
        key=key,
        priority=priority,
        rows=list(matched),
        extra={
            "note_ids": [n.id for n in matched],
            "row_count_initial": len(matched),
        },
        text=text,
        tokens_est=estimate_tokens(text),
        truncatable=truncatable,
    )


def _load_progress_notes_recent(raw: Any, notes: list[Note]) -> LoadedSource:
    limit = 3
    include_transcripts = False
    if isinstance(raw, dict):
        if "limit" in raw:
            try:
                limit = int(raw["limit"])
            except (TypeError, ValueError) as exc:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PROGRESS_NOTES_RECENT}.limit must be an integer"
                ) from exc
            if limit < 1 or limit > PROGRESS_NOTES_LIMIT_MAX:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PROGRESS_NOTES_RECENT}.limit must be between "
                    f"1 and {PROGRESS_NOTES_LIMIT_MAX}"
                )
        include_transcripts = bool(raw.get("include_transcripts", False))
    elif raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PROGRESS_NOTES_RECENT} selection must be true or "
            "{'limit': int, 'include_transcripts': bool}"
        )

    loaded = _load_notes_source(
        notes=notes,
        note_types=SESSION_NOTE_TYPES,
        key=SOURCE_KEY_PROGRESS_NOTES_RECENT,
        priority=6,
        header="Progress note",
        limit=limit,
        truncatable=True,
        section_prefix="RECENT PROGRESS NOTES",
    )
    loaded.extra["include_transcripts"] = include_transcripts
    loaded.extra["limit_requested"] = limit
    return loaded


def _load_progress_notes_explicit(raw: Any, notes: list[Note]) -> LoadedSource:
    if not isinstance(raw, dict) or "note_ids" not in raw:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PROGRESS_NOTES_EXPLICIT} selection must be {{'note_ids': [str, ...]}}"
        )
    note_ids = raw["note_ids"]
    if not isinstance(note_ids, list) or not all(isinstance(x, str) for x in note_ids):
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PROGRESS_NOTES_EXPLICIT}.note_ids must be a list of strings"
        )
    requested = set(note_ids)
    matched = [n for n in notes if n.id in requested]
    # Order by the caller's note_ids list so the assembler is deterministic.
    by_id = {n.id: n for n in matched}
    ordered = [by_id[nid] for nid in note_ids if nid in by_id]
    rendered = [_format_note_section(n, "Note") for n in ordered]
    rendered = [r for r in rendered if r]
    text = "## EXPLICITLY SELECTED NOTES\n\n" + "\n\n".join(rendered) if rendered else ""
    return LoadedSource(
        key=SOURCE_KEY_PROGRESS_NOTES_EXPLICIT,
        priority=5,
        rows=list(ordered),
        extra={
            "note_ids": [n.id for n in ordered],
            "missing_note_ids": [nid for nid in note_ids if nid not in by_id],
            "row_count_initial": len(ordered),
        },
        text=text,
        tokens_est=estimate_tokens(text),
        truncatable=True,
    )


def _load_most_recent_intake(raw: Any, notes: list[Note]) -> LoadedSource:
    if raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_MOST_RECENT_INTAKE} selection must be the boolean true"
        )
    return _load_notes_source(
        notes=notes,
        note_types=INTAKE_NOTE_TYPES,
        key=SOURCE_KEY_MOST_RECENT_INTAKE,
        priority=4,
        header="Intake",
        limit=1,
        truncatable=False,
        section_prefix="MOST RECENT INTAKE",
    )


def _load_treatment_plan_active(raw: Any, notes: list[Note]) -> LoadedSource:
    if raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_TREATMENT_PLAN_ACTIVE} selection must be the boolean true"
        )
    return _load_notes_source(
        notes=notes,
        note_types=TREATMENT_PLAN_NOTE_TYPES,
        key=SOURCE_KEY_TREATMENT_PLAN_ACTIVE,
        priority=7,
        header="Treatment plan",
        limit=1,
        truncatable=False,
        section_prefix="ACTIVE TREATMENT PLAN",
    )


def _load_safety_plan_active(raw: Any, notes: list[Note]) -> LoadedSource:
    if raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_SAFETY_PLAN_ACTIVE} selection must be the boolean true"
        )
    return _load_notes_source(
        notes=notes,
        note_types=SAFETY_PLAN_NOTE_TYPES,
        key=SOURCE_KEY_SAFETY_PLAN_ACTIVE,
        priority=3,
        header="Safety plan",
        limit=1,
        truncatable=False,
        section_prefix="ACTIVE SAFETY PLAN",
    )


def _load_current_medications(raw: Any, notes: list[Note]) -> LoadedSource:
    if raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_CURRENT_MEDICATIONS} selection must be the boolean true"
        )
    return _load_notes_source(
        notes=notes,
        note_types=MEDICATIONS_NOTE_TYPES,
        key=SOURCE_KEY_CURRENT_MEDICATIONS,
        priority=2,
        header="Medications",
        limit=1,
        truncatable=False,
        section_prefix="CURRENT MEDICATIONS",
    )


def _load_empty_stub(*, key: str, priority: int) -> LoadedSource:
    """Loader for sources whose underlying module hasn't shipped yet.

    Keeps the API contract stable so SaaS overlays can select these
    keys today; once the lab/vitals modules land in OSS, a follow-up
    PR replaces the stub with a real loader.
    """
    return LoadedSource(
        key=key,
        priority=priority,
        rows=[],
        extra={"row_count_initial": 0, "reason": "module_not_available"},
        text="",
        tokens_est=0,
        truncatable=False,
    )


def _load_lab_values_recent(raw: Any) -> LoadedSource:
    if isinstance(raw, dict):
        # Accept and ignore ``limit`` so the contract is forward-compatible
        # with the eventual real loader.
        pass
    elif raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_LAB_VALUES_RECENT} selection must be true or {{'limit': int}}"
        )
    return _load_empty_stub(
        key=SOURCE_KEY_LAB_VALUES_RECENT,
        priority=8,
    )


def _load_vitals_recent(raw: Any) -> LoadedSource:
    if isinstance(raw, dict):
        pass
    elif raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_VITALS_RECENT} selection must be true or {{'limit': int}}"
        )
    return _load_empty_stub(
        key=SOURCE_KEY_VITALS_RECENT,
        priority=8,
    )


# ---------------------------------------------------------------------------
# Assembler
# ---------------------------------------------------------------------------


def _is_truthy(raw: Any) -> bool:
    """Return True if a selection value indicates the source is active.

    Booleans gate the simple sources. Dict shapes are active when
    present (the inner validation happens inside each loader).
    """
    return not (raw is None or raw is False)


def _load_selected_sources(
    *,
    selection: dict[str, Any],
    notes: list[Note],
) -> list[LoadedSource]:
    loaded: list[LoadedSource] = []
    for key, raw in selection.items():
        if key not in V1_SOURCE_KEYS:
            raise InvalidSelectionError(f"unknown source key {key!r}")
        if not _is_truthy(raw):
            continue
        if key == SOURCE_KEY_PASTED_TEXT:
            loaded.append(_load_pasted_text(raw))
        elif key == SOURCE_KEY_CURRENT_MEDICATIONS:
            loaded.append(_load_current_medications(raw, notes))
        elif key == SOURCE_KEY_MOST_RECENT_INTAKE:
            loaded.append(_load_most_recent_intake(raw, notes))
        elif key == SOURCE_KEY_PROGRESS_NOTES_RECENT:
            loaded.append(_load_progress_notes_recent(raw, notes))
        elif key == SOURCE_KEY_PROGRESS_NOTES_EXPLICIT:
            loaded.append(_load_progress_notes_explicit(raw, notes))
        elif key == SOURCE_KEY_TREATMENT_PLAN_ACTIVE:
            loaded.append(_load_treatment_plan_active(raw, notes))
        elif key == SOURCE_KEY_SAFETY_PLAN_ACTIVE:
            loaded.append(_load_safety_plan_active(raw, notes))
        elif key == SOURCE_KEY_LAB_VALUES_RECENT:
            loaded.append(_load_lab_values_recent(raw))
        elif key == SOURCE_KEY_VITALS_RECENT:
            loaded.append(_load_vitals_recent(raw))
    return loaded


def _truncate_one_row(source: LoadedSource) -> bool:
    """Drop the oldest row from a row-truncatable source.

    Returns True if it dropped a row, False if it can't shrink further.
    Mutates ``source`` in place: ``rows``, ``text``, ``tokens_est``,
    ``extra['dropped_rows']``.
    """
    if not source.truncatable or not source.rows:
        return False
    dropped = source.rows.pop()  # NotesRepository returns newest-first; pop oldest
    source.extra.setdefault("dropped_rows", 0)
    source.extra["dropped_rows"] += 1
    if hasattr(dropped, "id"):
        source.extra.setdefault("dropped_note_ids", []).append(dropped.id)
        source.extra["note_ids"] = [n.id for n in source.rows]
    # Re-render. Recompute header per source key.
    if source.key == SOURCE_KEY_PROGRESS_NOTES_RECENT:
        header = "Progress note"
        section = "RECENT PROGRESS NOTES"
    elif source.key == SOURCE_KEY_PROGRESS_NOTES_EXPLICIT:
        header = "Note"
        section = "EXPLICITLY SELECTED NOTES"
    else:
        header = source.key.replace("_", " ").title()
        section = source.key.upper().replace("_", " ")
    rendered = [_format_note_section(n, header) for n in source.rows]
    rendered = [r for r in rendered if r]
    source.text = f"## {section}\n\n" + "\n\n".join(rendered) if rendered else ""
    source.tokens_est = estimate_tokens(source.text)
    return True


def _drop_source(source: LoadedSource, reason: str) -> None:
    source.dropped_reason = reason
    source.text = ""
    source.tokens_est = 0


def _enforce_budget(loaded: list[LoadedSource], token_budget: int) -> None:
    """In-place enforcement of the token budget.

    Walks sources in reverse priority order. Row-truncatable sources
    shed rows until they fit or are empty; non-truncatable sources are
    dropped wholesale. The pasted-text source has priority 1 and is
    never touched here — overflow on pasted text alone is caught
    upstream by :func:`assemble_context_bundle` and raised as
    :class:`ContextOverflowError`.
    """
    total = sum(src.tokens_est for src in loaded if src.is_present)
    if total <= token_budget:
        return

    # Reverse priority order: higher priority numbers first.
    ordered = sorted(
        [s for s in loaded if s.is_present and s.key != SOURCE_KEY_PASTED_TEXT],
        key=lambda s: -s.priority,
    )
    for src in ordered:
        while total > token_budget and src.is_present and src.tokens_est > 0:
            if src.truncatable and _truncate_one_row(src):
                total = sum(s.tokens_est for s in loaded if s.is_present)
                if not src.rows:
                    _drop_source(src, "budget")
                    total = sum(s.tokens_est for s in loaded if s.is_present)
                    break
            else:
                _drop_source(src, "budget")
                total = sum(s.tokens_est for s in loaded if s.is_present)
                break
        if total <= token_budget:
            return


def _build_manifest(
    *,
    loaded: list[LoadedSource],
    patient_id: str,
    token_budget: int,
    total_tokens_est: int,
) -> dict[str, Any]:
    included: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for src in loaded:
        entry: dict[str, Any] = {
            "source_key": src.key,
            "tokens_est": src.tokens_est,
            "row_count": len(src.rows) if src.is_present else 0,
        }
        # Forensic extras (no PHI content — ids and counts only).
        note_ids = src.extra.get("note_ids")
        if note_ids:
            entry["note_ids"] = list(note_ids)
        if src.key == SOURCE_KEY_PASTED_TEXT and src.is_present:
            entry["chars"] = src.extra.get("chars", 0)
        if src.extra.get("dropped_rows"):
            entry["rows_dropped"] = src.extra["dropped_rows"]
            if src.extra.get("dropped_note_ids"):
                entry["dropped_note_ids"] = list(src.extra["dropped_note_ids"])
        if src.is_present:
            included.append(entry)
        else:
            dropped.append({"source_key": src.key, "reason": src.dropped_reason})
        # Stub sources (lab/vitals when modules don't exist) report
        # row_count=0 and a "reason" without being "dropped".
        if src.is_present and src.extra.get("reason") == "module_not_available":
            entry["reason"] = "module_not_available"
    return {
        "sources_included": included,
        "sources_dropped": dropped,
        "total_tokens_est": total_tokens_est,
        "token_budget": token_budget,
        "patient_id": patient_id,
        "assembled_at": utc_now_iso(),
    }


def _build_text(loaded: list[LoadedSource]) -> str:
    sections: list[str] = []
    for src in sorted(loaded, key=lambda s: s.priority):
        section = src.render_section()
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def assemble_context_bundle(
    *,
    notes_repo: NotesRepository,
    patient_id: str,
    user_id: str,
    selection: dict[str, Any],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> ContextBundle:
    """Assemble a context bundle for one chat turn.

    See module docstring for the high-level algorithm. ``selection`` is
    the dict shape persisted on ``chat_conversations.default_source_selection``
    (or the per-message override). Unknown source keys raise
    :class:`InvalidSelectionError`; recognized keys with falsy values
    are skipped.

    Raises :class:`ContextOverflowError` only when the pasted-text
    source alone exceeds ``token_budget``. All other budget pressure is
    resolved silently via the truncation policy and surfaced in the
    manifest.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    notes = notes_repo.list_by_patient(patient_id, user_id)
    loaded = _load_selected_sources(selection=selection, notes=notes)

    pasted = next((s for s in loaded if s.key == SOURCE_KEY_PASTED_TEXT), None)
    if pasted is not None and pasted.tokens_est > token_budget:
        raise ContextOverflowError(pasted_tokens=pasted.tokens_est, token_budget=token_budget)

    _enforce_budget(loaded, token_budget)
    text = _build_text(loaded)
    total_tokens_est = estimate_tokens(text)
    manifest = _build_manifest(
        loaded=loaded,
        patient_id=patient_id,
        token_budget=token_budget,
        total_tokens_est=total_tokens_est,
    )
    return ContextBundle(
        text=text,
        manifest=manifest,
        total_tokens_est=total_tokens_est,
    )


# ---------------------------------------------------------------------------
# Default selection
# ---------------------------------------------------------------------------


def default_source_selection() -> dict[str, Any]:
    """Return the design-doc §7.4 recommended default selection.

    Used when a caller creates a conversation without specifying one.
    SaaS overlays may override per ``caller_feature_key``.
    """
    return {
        SOURCE_KEY_CURRENT_MEDICATIONS: True,
        SOURCE_KEY_MOST_RECENT_INTAKE: True,
        SOURCE_KEY_PROGRESS_NOTES_RECENT: {
            "limit": 3,
            "include_transcripts": False,
        },
        SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True,
        SOURCE_KEY_SAFETY_PLAN_ACTIVE: True,
        SOURCE_KEY_LAB_VALUES_RECENT: {"limit": 5},
        SOURCE_KEY_VITALS_RECENT: {"limit": 5},
    }


__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_TOKEN_BUDGET",
    "INTAKE_NOTE_TYPES",
    "MEDICATIONS_NOTE_TYPES",
    "PASTED_TEXT_MAX_CHARS",
    "SAFETY_PLAN_NOTE_TYPES",
    "SESSION_NOTE_TYPES",
    "SOURCE_KEY_CURRENT_MEDICATIONS",
    "SOURCE_KEY_LAB_VALUES_RECENT",
    "SOURCE_KEY_MOST_RECENT_INTAKE",
    "SOURCE_KEY_PASTED_TEXT",
    "SOURCE_KEY_PROGRESS_NOTES_EXPLICIT",
    "SOURCE_KEY_PROGRESS_NOTES_RECENT",
    "SOURCE_KEY_SAFETY_PLAN_ACTIVE",
    "SOURCE_KEY_TREATMENT_PLAN_ACTIVE",
    "SOURCE_KEY_VITALS_RECENT",
    "TREATMENT_PLAN_NOTE_TYPES",
    "V1_SOURCE_KEYS",
    "ContextBundle",
    "ContextOverflowError",
    "InvalidSelectionError",
    "assemble_context_bundle",
    "default_source_selection",
    "estimate_tokens",
]
