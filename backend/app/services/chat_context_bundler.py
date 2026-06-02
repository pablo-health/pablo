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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..utcnow import utc_now_iso

if TYPE_CHECKING:
    from ..models import Note, PatientDocument
    from ..repositories import NotesRepository, PatientDocumentRepository


# ---------------------------------------------------------------------------
# Source keys + note-type bindings
# ---------------------------------------------------------------------------

# Source keys exposed to callers. Frozen tuple so it's hashable and
# stable across imports — downstream consumers may import this set
# for tier gating in ``caller_feature_key`` enforcement.
SOURCE_KEY_PASTED_TEXT = "pasted_text"
SOURCE_KEY_CURRENT_MEDICATIONS = "current_medications"
SOURCE_KEY_MOST_RECENT_INTAKE = "most_recent_intake"
SOURCE_KEY_PROGRESS_NOTES_RECENT = "progress_notes_recent"
SOURCE_KEY_PROGRESS_NOTES_EXPLICIT = "progress_notes_explicit"
SOURCE_KEY_DOCUMENT_MANIFEST = "document_manifest"
SOURCE_KEY_PATIENT_DOCUMENTS = "patient_documents"
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
    SOURCE_KEY_DOCUMENT_MANIFEST,
    SOURCE_KEY_PATIENT_DOCUMENTS,
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

# Upper bound on ``patient_documents.limit`` — mirrors the progress-notes
# cap. Uploaded chart artifacts can be large (multi-MB intake PDFs); the
# truncation walk shrinks the source row-by-row if it can't fit, but the
# explicit cap rejects a runaway ``limit`` upfront.
PATIENT_DOCUMENTS_LIMIT_MAX = 50

# Per-document render cap. The existing truncation walk only drops *whole*
# docs once the source can't fit the budget — fine for a chart with several
# docs, but a single 200-page intake PDF would either consume the entire
# budget or get dropped wholesale, leaving the clinician with "I don't
# know." The cap clips any one doc to ~80k tokens (320k chars at the
# bundler's 4-char heuristic) with an explicit truncation marker, so a
# long doc contributes its first N pages instead of all-or-nothing. The
# downstream budget walk still runs after this cap.
PATIENT_DOCUMENT_MAX_RENDER_CHARS = 320_000

# Priority for the document-manifest source. It sits just above
# ``patient_documents`` (priority 6) so the compact index of every
# uploaded doc renders before — and survives budget pressure longer
# than — the full document bodies. It is a tiny, non-truncatable index,
# so keeping it costs almost nothing while giving the model an
# always-present catalogue of what is on file even when the full bodies
# get truncated or dropped. (5 also keys ``progress_notes_explicit``;
# equal priorities are permitted — see lab/vitals both at 9 — and the
# manifest's near-zero token cost makes the tie harmless.)
DOCUMENT_MANIFEST_PRIORITY = 5

# Per-document preview length for the manifest index line. Short enough
# that the whole catalogue stays a cheap, always-present index even for
# a chart with many docs.
DOCUMENT_MANIFEST_PREVIEW_CHARS = 200

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
class RetrievedDocument:
    """One retrieved item that contributed to the assembled context.

    The structured, per-item counterpart to the flattened ``text`` blob:
    a single note, patient document, or pasted block — reflecting the
    *final* (post-truncation) set the model actually received. Carrying
    each item's id and text separately is what lets retrieval quality be
    evaluated per document (relevance to the question) rather than only
    in aggregate.
    """

    source_key: str
    """Which selection source produced this item (e.g. ``progress_notes_recent``)."""

    document_id: str
    """Chart id of the item (note id / patient-document id) where one
    exists; the source key for synthesised single-block sources."""

    text: str
    """The item's rendered content (chart material — treat as PHI)."""

    tokens_est: int
    """Estimated tokens for ``text``."""


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

    documents: tuple[RetrievedDocument, ...] = ()
    """Per-item breakdown of the retrieved context, in assembled order.
    Empty when no source had content. Derived from the same final source
    rows as ``text``; carries chart material, so it is never persisted on
    the (PHI-free) manifest."""


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


def _latest_iso(notes: list[Note]) -> str | None:
    """ISO timestamp of the most-recently-finalized note in ``notes``.

    Used by the briefing-card preview so the UI can render an
    accurate "last from <date>" without re-fetching the notes list
    client-side. Falls back through finalized_at → updated_at →
    created_at so any note that hasn't been finalized still surfaces
    a sensible date.
    """
    if not notes:
        return None
    candidates = [(n.finalized_at or n.updated_at or n.created_at) for n in notes]
    return max(candidates).isoformat()


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
    extra: dict[str, Any] = {
        "note_ids": [n.id for n in matched],
        "row_count_initial": len(matched),
    }
    latest_at = _latest_iso(matched)
    if latest_at is not None:
        extra["latest_at"] = latest_at
    return LoadedSource(
        key=key,
        priority=priority,
        rows=list(matched),
        extra=extra,
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
        priority=7,
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
    extra: dict[str, Any] = {
        "note_ids": [n.id for n in ordered],
        "missing_note_ids": [nid for nid in note_ids if nid not in by_id],
        "row_count_initial": len(ordered),
    }
    latest_at = _latest_iso(ordered)
    if latest_at is not None:
        extra["latest_at"] = latest_at
    return LoadedSource(
        key=SOURCE_KEY_PROGRESS_NOTES_EXPLICIT,
        priority=5,
        rows=list(ordered),
        extra=extra,
        text=text,
        tokens_est=estimate_tokens(text),
        truncatable=True,
    )


def _score_doc_relevance(doc_text: str, query: str) -> float:
    """Overlap coefficient between a document's words and the query's words.

    Lowercased whitespace-token sets; ``len(a & b) / min(len(a), len(b))``.
    A cheap, dependency-free relevance proxy used to order patient
    documents so the most query-relevant docs render first (and thus
    survive budget truncation, which drops from the tail). Returns
    ``0.0`` when the query is empty or either token set is empty so a
    missing query degrades gracefully to load-order (newest-first).

    Overlap coefficient (not Jaccard) on purpose: Jaccard's union
    denominator grows with document length, so a long, highly-relevant
    note would score *lower* than a short tangentially-relevant one —
    backwards for our goal of keeping the most relevant doc. Dividing by
    the smaller set (in practice the query) removes that length bias.
    """
    if not query:
        return 0.0
    a = set(doc_text.lower().split())
    b = set(query.lower().split())
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    smaller = min(len(a), len(b))
    return intersection / smaller


def _manifest_preview(doc: PatientDocument) -> str:
    """One-line preview for a doc's manifest index entry.

    Prefers a stored AI summary (``extraction_metadata['summary']``),
    falls back to the head of the extracted text, then to a placeholder.
    Never raises on a malformed ``extraction_metadata`` blob.
    """
    metadata = doc.extraction_metadata or {}
    summary = metadata.get("summary") if isinstance(metadata, dict) else None
    if isinstance(summary, str) and summary.strip():
        return " ".join(summary.split())
    body = (doc.extracted_text or "").strip()
    if body:
        excerpt = body[:DOCUMENT_MANIFEST_PREVIEW_CHARS]
        return " ".join(excerpt.split())
    return "(no preview)"


def _load_document_manifest(docs: list[PatientDocument]) -> LoadedSource:
    """Build a compact index of every patient document on file.

    One line per document — title/filename, date, and a short
    summary/excerpt — so the model always knows the full set of uploaded
    documents even when their full bodies are truncated or evicted under
    budget pressure. Non-truncatable: the whole index is kept or nothing.

    ``docs`` is the same list :func:`_load_patient_documents` renders in
    full; the manifest indexes all of them, including docs without
    extracted text (those show ``(no preview)``) so the model can still
    surface that a scanned/image doc exists.
    """
    lines: list[str] = []
    for doc in docs:
        title = getattr(doc, "title", None) or doc.filename
        upload_date = getattr(doc, "upload_date", None) or doc.created_at.date()
        preview = _manifest_preview(doc)
        lines.append(f"- {title} · {upload_date} · {preview}")
    text = "## PATIENT DOCUMENTS ON FILE\n" + "\n".join(lines) if lines else ""
    return LoadedSource(
        key=SOURCE_KEY_DOCUMENT_MANIFEST,
        priority=DOCUMENT_MANIFEST_PRIORITY,
        rows=list(docs),
        extra={
            "document_ids": [d.id for d in docs],
            "row_count_initial": len(docs),
        },
        text=text,
        tokens_est=estimate_tokens(text),
        truncatable=False,
    )


def _format_patient_document_section(doc: PatientDocument) -> str:
    """Render one uploaded document as a subsection block.

    Header carries the filename + upload date so the model can attribute
    quotes back to a specific document; body is the extracted text. Docs
    without extracted text (scanned PDFs awaiting OCR — ak6m.2.3) never
    reach this function — :func:`_load_patient_documents` filters them
    upstream and counts them under ``skipped_no_text``.

    Bodies over ``PATIENT_DOCUMENT_MAX_RENDER_CHARS`` are clipped with an
    explicit ``[document truncated — ...]`` marker so the model can tell
    the difference between a doc that genuinely says nothing further and
    one whose tail got cut for budget reasons. When the doc carries a
    stored AI summary (``extraction_metadata['summary']``) we render that
    summary instead of a blind head-clip, so an over-cap document
    contributes a faithful whole-document gist rather than just its first
    pages.
    """
    uploaded = doc.created_at.date()
    body = (doc.extracted_text or "").strip()
    if not body:
        return ""
    original_chars = len(body)
    if original_chars > PATIENT_DOCUMENT_MAX_RENDER_CHARS:
        metadata = doc.extraction_metadata or {}
        summary = metadata.get("summary") if isinstance(metadata, dict) else None
        if isinstance(summary, str) and summary.strip():
            body = (
                f"[SUMMARY — full document loaded in brief; original was "
                f"{original_chars} chars]\n{summary.strip()}"
            )
        else:
            omitted = original_chars - PATIENT_DOCUMENT_MAX_RENDER_CHARS
            body = (
                body[:PATIENT_DOCUMENT_MAX_RENDER_CHARS]
                + f"\n\n[document truncated — {omitted} chars omitted; "
                f"original was {original_chars} chars]"
            )
    return f"### {doc.filename} (uploaded {uploaded})\n{body}"


def _render_patient_documents_text(rows: list[PatientDocument]) -> str:
    rendered = [_format_patient_document_section(d) for d in rows]
    rendered = [r for r in rendered if r]
    if not rendered:
        return ""
    return "## UPLOADED PATIENT DOCUMENTS\n\n" + "\n\n".join(rendered)


# ---------------------------------------------------------------------------
# Document rendering strategies (extension seam)
# ---------------------------------------------------------------------------

# A document strategy renders the access-checked, relevance-ordered documents
# for a turn into the text block that goes into the prompt. The signature is
# fixed — ``list[PatientDocument] -> str`` over the *final* (possibly
# budget-truncated) row set — so the truncation, manifest, and budget code
# stay strategy-agnostic: the budget walk drops a row and re-renders through
# the same strategy without knowing which one it is.
#
# The engine ships exactly one strategy, ``raw_text`` (full extracted text
# with the per-doc render cap + summary fallback from §7.9 of the design
# doc). A deployment can register additional strategies at import time —
# e.g. summary-only, structured-field, or retrieval-augmented rendering —
# via :func:`register_document_strategy`; the per-source ``strategy`` field
# then selects one. This is a deliberate seam, not a plugin framework: a
# strategy that needs its own tools or a multi-step fetch loop requires an
# additional turn-service hook, not just a renderer.
DocumentRenderer = Callable[[list["PatientDocument"]], str]

DEFAULT_DOCUMENT_STRATEGY = "raw_text"

_DOCUMENT_RENDERERS: dict[str, DocumentRenderer] = {}


def register_document_strategy(
    name: str, renderer: DocumentRenderer, *, replace: bool = False
) -> None:
    """Register a patient-document rendering strategy under ``name``.

    ``renderer`` takes the final ordered documents and returns the rendered
    text block. Raises ``ValueError`` if ``name`` is already registered and
    ``replace`` is not set, so an overlay can't silently shadow a strategy.
    """
    if name in _DOCUMENT_RENDERERS and not replace:
        raise ValueError(f"document strategy {name!r} is already registered")
    _DOCUMENT_RENDERERS[name] = renderer


def _document_renderer(name: str) -> DocumentRenderer:
    try:
        return _DOCUMENT_RENDERERS[name]
    except KeyError:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PATIENT_DOCUMENTS}.strategy {name!r} is not registered"
        ) from None


register_document_strategy(DEFAULT_DOCUMENT_STRATEGY, _render_patient_documents_text)


def _load_patient_documents(
    raw: Any,
    *,
    patient_documents_repo: PatientDocumentRepository,
    patient_id: str,
    user_id: str,
    query: str | None = None,
) -> LoadedSource:
    limit: int | None = None
    explicit_ids: list[str] | None = None
    strategy_name = DEFAULT_DOCUMENT_STRATEGY

    if isinstance(raw, dict):
        has_limit = "limit" in raw
        has_doc_ids = "document_ids" in raw
        if has_limit and has_doc_ids:
            raise InvalidSelectionError(
                f"{SOURCE_KEY_PATIENT_DOCUMENTS}: 'limit' and 'document_ids' are mutually exclusive"
            )
        if "strategy" in raw:
            if not isinstance(raw["strategy"], str):
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PATIENT_DOCUMENTS}.strategy must be a string"
                )
            strategy_name = raw["strategy"]
        if has_limit:
            try:
                limit = int(raw["limit"])
            except (TypeError, ValueError) as exc:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PATIENT_DOCUMENTS}.limit must be an integer"
                ) from exc
            if limit < 1 or limit > PATIENT_DOCUMENTS_LIMIT_MAX:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PATIENT_DOCUMENTS}.limit must be between "
                    f"1 and {PATIENT_DOCUMENTS_LIMIT_MAX}"
                )
        if has_doc_ids:
            ids = raw["document_ids"]
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PATIENT_DOCUMENTS}.document_ids must be a list of strings"
                )
            explicit_ids = list(ids)
    elif raw is not True:
        raise InvalidSelectionError(
            f"{SOURCE_KEY_PATIENT_DOCUMENTS} selection must be true, "
            "{'limit': int}, or {'document_ids': [str, ...]}"
        )

    # Resolve the rendering strategy before the RLS fetch so an unknown
    # strategy fails fast without a wasted query.
    renderer = _document_renderer(strategy_name)

    if explicit_ids is not None:
        # Single bulk fetch rather than one query per id. Preserve
        # caller-supplied order and silently skip ids the caller cannot
        # read or that belong to a different patient — matches the
        # ``progress_notes_explicit`` contract (no existence oracle on a
        # forbidden id).
        fetched = patient_documents_repo.get_many(explicit_ids, user_id)
        by_id = {d.id: d for d in fetched if d.patient_id == patient_id}
        all_for_patient = [by_id[did] for did in explicit_ids if did in by_id]
    else:
        all_for_patient = patient_documents_repo.list_for_patient(patient_id, user_id)
        if limit is not None:
            all_for_patient = all_for_patient[:limit]

    skipped_no_text = sum(1 for d in all_for_patient if d.extracted_text is None)
    usable = [d for d in all_for_patient if d.extracted_text is not None]
    # Relevance ordering: when the turn carries a query, sort usable docs
    # by overlap-coefficient score against it (DESC) so the most relevant
    # docs render first. The budget walk drops rows from the *tail*, so the highest-
    # scoring docs survive truncation longest. ``sorted`` is stable, so
    # ties (and the query-less case, where all scores are 0.0) preserve
    # the repo's newest-first order.
    if query:
        usable = sorted(
            usable,
            key=lambda d: _score_doc_relevance(d.extracted_text or "", query),
            reverse=True,
        )
    text = renderer(usable)
    extra: dict[str, Any] = {
        "document_ids": [d.id for d in usable],
        "row_count_initial": len(usable),
        "skipped_no_text": skipped_no_text,
        "strategy": strategy_name,
    }
    return LoadedSource(
        key=SOURCE_KEY_PATIENT_DOCUMENTS,
        priority=6,
        rows=list(usable),
        extra=extra,
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
        priority=8,
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

    Keeps the API contract stable so downstream consumers can select
    these keys today; once the lab/vitals modules land, a follow-up
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
        priority=9,
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
        priority=9,
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


def _load_selected_sources(  # noqa: PLR0912 — one dispatch arm per source key
    *,
    selection: dict[str, Any],
    notes: list[Note],
    patient_documents_repo: PatientDocumentRepository | None,
    patient_id: str,
    user_id: str,
    query: str | None = None,
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
        elif key == SOURCE_KEY_DOCUMENT_MANIFEST:
            if patient_documents_repo is None:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_DOCUMENT_MANIFEST} was selected but the "
                    "bundler was not given a patient_documents_repo"
                )
            docs = patient_documents_repo.list_for_patient(patient_id, user_id)
            loaded.append(_load_document_manifest(docs))
        elif key == SOURCE_KEY_PATIENT_DOCUMENTS:
            if patient_documents_repo is None:
                raise InvalidSelectionError(
                    f"{SOURCE_KEY_PATIENT_DOCUMENTS} was selected but the "
                    "bundler was not given a patient_documents_repo"
                )
            loaded.append(
                _load_patient_documents(
                    raw,
                    patient_documents_repo=patient_documents_repo,
                    patient_id=patient_id,
                    user_id=user_id,
                    query=query,
                )
            )
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
    dropped = source.rows.pop()  # repos return newest-first; pop oldest
    source.extra.setdefault("dropped_rows", 0)
    source.extra["dropped_rows"] += 1
    if source.key == SOURCE_KEY_PATIENT_DOCUMENTS:
        if hasattr(dropped, "id"):
            source.extra.setdefault("dropped_document_ids", []).append(dropped.id)
            source.extra["document_ids"] = [d.id for d in source.rows]
        # Re-render through the same strategy the source was loaded with so
        # truncation stays strategy-agnostic.
        renderer = _document_renderer(source.extra.get("strategy", DEFAULT_DOCUMENT_STRATEGY))
        source.text = renderer(source.rows)
        source.tokens_est = estimate_tokens(source.text)
        return True
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
        # Forensic extras (no PHI content — ids, counts, dates only).
        note_ids = src.extra.get("note_ids")
        if note_ids:
            entry["note_ids"] = list(note_ids)
        document_ids = src.extra.get("document_ids")
        if document_ids is not None and src.key in (
            SOURCE_KEY_PATIENT_DOCUMENTS,
            SOURCE_KEY_DOCUMENT_MANIFEST,
        ):
            entry["document_ids"] = list(document_ids)
        if src.key == SOURCE_KEY_PATIENT_DOCUMENTS and src.is_present:
            entry["skipped_no_text"] = src.extra.get("skipped_no_text", 0)
            entry["strategy"] = src.extra.get("strategy", DEFAULT_DOCUMENT_STRATEGY)
        latest_at = src.extra.get("latest_at")
        if latest_at:
            entry["latest_at"] = latest_at
        if src.key == SOURCE_KEY_PASTED_TEXT and src.is_present:
            entry["chars"] = src.extra.get("chars", 0)
        if src.extra.get("dropped_rows"):
            entry["rows_dropped"] = src.extra["dropped_rows"]
            if src.extra.get("dropped_note_ids"):
                entry["dropped_note_ids"] = list(src.extra["dropped_note_ids"])
            if src.extra.get("dropped_document_ids"):
                entry["dropped_document_ids"] = list(src.extra["dropped_document_ids"])
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


def _document_text_for_row(source_key: str, row: Any) -> tuple[str, str]:
    """Return ``(document_id, text)`` for one final source row.

    Renders the row with the same single-item renderer the section build
    uses, so the per-document text reflects exactly what survived
    truncation. Dispatches on source: pasted text is a raw string row,
    patient documents are :class:`PatientDocument`, everything else is a
    note-backed :class:`Note`.
    """
    if source_key == SOURCE_KEY_PASTED_TEXT:
        return SOURCE_KEY_PASTED_TEXT, row if isinstance(row, str) else str(row)
    if source_key == SOURCE_KEY_PATIENT_DOCUMENTS:
        return row.id, _format_patient_document_section(row)
    return row.id, _note_display_text(row)


def _documents_from_loaded(loaded: list[LoadedSource]) -> tuple[RetrievedDocument, ...]:
    """Build the per-document breakdown from the final loaded sources.

    Read-only: walks each present source's surviving rows (post-budget)
    in the same priority order as ``_build_text`` and renders each item.
    Rows that render empty are skipped, matching the section build.

    The ``document_manifest`` source is a synthesized index over the same
    docs ``patient_documents`` already breaks out per item, so it is
    emitted as a single synthetic block (keyed by its source key) rather
    than re-listing each doc — its rows are :class:`PatientDocument`
    objects that the per-row note renderer can't handle.
    """
    documents: list[RetrievedDocument] = []
    for src in sorted(loaded, key=lambda s: s.priority):
        if not src.is_present:
            continue
        if src.key == SOURCE_KEY_DOCUMENT_MANIFEST:
            text = src.text.strip()
            if text:
                documents.append(
                    RetrievedDocument(
                        source_key=src.key,
                        document_id=src.key,
                        text=text,
                        tokens_est=estimate_tokens(text),
                    )
                )
            continue
        for row in src.rows:
            document_id, text = _document_text_for_row(src.key, row)
            text = text.strip()
            if not text:
                continue
            documents.append(
                RetrievedDocument(
                    source_key=src.key,
                    document_id=document_id,
                    text=text,
                    tokens_est=estimate_tokens(text),
                )
            )
    return tuple(documents)


def assemble_context_bundle(
    *,
    notes_repo: NotesRepository,
    patient_id: str,
    user_id: str,
    selection: dict[str, Any],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    patient_documents_repo: PatientDocumentRepository | None = None,
    query: str | None = None,
) -> ContextBundle:
    """Assemble a context bundle for one chat turn.

    See module docstring for the high-level algorithm. ``selection`` is
    the dict shape persisted on ``chat_conversations.default_source_selection``
    (or the per-message override). Unknown source keys raise
    :class:`InvalidSelectionError`; recognized keys with falsy values
    are skipped.

    Pass ``patient_documents_repo`` when callers may select the
    ``patient_documents`` source. The bundler does not import the
    Postgres impl at module load; callers thread the concrete repo via
    their dependency injection chain. Selecting the source without
    supplying a repo raises :class:`InvalidSelectionError` — the same
    surface a misconfigured selection produces.

    Raises :class:`ContextOverflowError` only when the pasted-text
    source alone exceeds ``token_budget``. All other budget pressure is
    resolved silently via the truncation policy and surfaced in the
    manifest.

    ``query`` is the caller's current turn text, when available. It is
    used only to relevance-order the ``patient_documents`` source (most
    query-relevant docs first, so they survive budget truncation
    longest); it does not select or filter sources, and it is never
    persisted on the PHI-free manifest. ``None`` (e.g. the briefing-card
    preview, which has no user turn yet) leaves docs in newest-first
    order.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be positive")

    # Cap the chart-note fetch on the chat hot path. Every selected
    # note-backed source (intake, treatment/safety plan, medications,
    # progress notes) draws from this one list; the per-source loaders
    # then filter by type and apply their own tighter limits. The cap
    # keeps a patient with a very long note history from loading the
    # entire chart on every turn — the budget walk and per-source limits
    # already mean only the most-recent notes survive into the prompt.
    notes = notes_repo.list_by_patient(
        patient_id, user_id, limit=PROGRESS_NOTES_LIMIT_MAX
    )
    loaded = _load_selected_sources(
        selection=selection,
        notes=notes,
        patient_documents_repo=patient_documents_repo,
        patient_id=patient_id,
        user_id=user_id,
        query=query,
    )

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
        documents=_documents_from_loaded(loaded),
    )


# ---------------------------------------------------------------------------
# Default selection
# ---------------------------------------------------------------------------


def default_source_selection() -> dict[str, Any]:
    """Return the design-doc §7.4 recommended default selection.

    Used when a caller creates a conversation without specifying one.
    Downstream consumers may override per ``caller_feature_key``.
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
    "DEFAULT_DOCUMENT_STRATEGY",
    "DEFAULT_TOKEN_BUDGET",
    "DOCUMENT_MANIFEST_PREVIEW_CHARS",
    "DOCUMENT_MANIFEST_PRIORITY",
    "INTAKE_NOTE_TYPES",
    "MEDICATIONS_NOTE_TYPES",
    "PASTED_TEXT_MAX_CHARS",
    "PATIENT_DOCUMENTS_LIMIT_MAX",
    "PATIENT_DOCUMENT_MAX_RENDER_CHARS",
    "PROGRESS_NOTES_LIMIT_MAX",
    "SAFETY_PLAN_NOTE_TYPES",
    "SESSION_NOTE_TYPES",
    "SOURCE_KEY_CURRENT_MEDICATIONS",
    "SOURCE_KEY_DOCUMENT_MANIFEST",
    "SOURCE_KEY_LAB_VALUES_RECENT",
    "SOURCE_KEY_MOST_RECENT_INTAKE",
    "SOURCE_KEY_PASTED_TEXT",
    "SOURCE_KEY_PATIENT_DOCUMENTS",
    "SOURCE_KEY_PROGRESS_NOTES_EXPLICIT",
    "SOURCE_KEY_PROGRESS_NOTES_RECENT",
    "SOURCE_KEY_SAFETY_PLAN_ACTIVE",
    "SOURCE_KEY_TREATMENT_PLAN_ACTIVE",
    "SOURCE_KEY_VITALS_RECENT",
    "TREATMENT_PLAN_NOTE_TYPES",
    "V1_SOURCE_KEYS",
    "ContextBundle",
    "ContextOverflowError",
    "DocumentRenderer",
    "InvalidSelectionError",
    "assemble_context_bundle",
    "default_source_selection",
    "estimate_tokens",
    "register_document_strategy",
]
