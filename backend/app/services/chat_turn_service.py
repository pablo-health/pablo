# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Streaming turn service for patient-context chat (THERAPY-5x5).

Owns the per-turn lifecycle: assemble context, persist the user row,
construct the prompt envelope, stream the assistant response from the
Gemini gateway, persist final assistant content + token counts, and
bump the conversation's ``last_turn_at`` timestamp.

Phase 2 (the context bundle assembler) is invoked here for every
turn. Phase 1 owns conversation lifecycle. Phase 3 (this module) is
the hot path; everything else exists in service of it.

The service yields a typed :class:`TurnStreamEvent` sequence; the SSE
route translates each event into the wire-format frames spelled out
in design doc §6.4. Retry policy lives at this layer (one retry on
transient gateway error per §8); safety blocks return immediately.

No PHI lands in logs. The conversation id, user id, model id, and
token counts are safe to log; everything else stays on the
``chat_messages`` row.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

from ..models import ChatMessage, QuotaStatus
from ..utcnow import utc_now
from .chat_context_bundler import (
    ContextBundle,
    ContextOverflowError,
    InvalidSelectionError,
    assemble_context_bundle,
    default_source_selection,
)
from .chat_llm_gateway import StreamEvent, UserAssistantTurn
from .llm_telemetry import RetrievedDocumentRef, retrieval_span

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..medications.repository import MedicationRepository
    from ..repositories import ChatRepository, NotesRepository, PatientDocumentRepository
    from .chat_llm_gateway import ChatLLMGateway
    from .llm_usage_meter import LlmUsageMeter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


TurnEventKind = Literal["meta", "delta", "done", "error"]


@dataclass(frozen=True)
class TurnStreamEvent:
    """One event yielded by :meth:`ChatTurnService.run_turn`.

    Mirrors the SSE wire shape one-to-one so the route layer is a thin
    JSON-encoder; behavior decisions stay in the service.
    """

    kind: TurnEventKind
    data: dict[str, object]


@dataclass(frozen=True)
class TurnContext:
    """Static per-turn inputs the service needs.

    Passed by the route after authorization. The service trusts the
    route to have verified that ``requesting_user_id`` has a
    :func:`has_patient_access` grant on ``patient_id``.

    ``requesting_user_id`` is the clinician issuing *this* turn — not
    necessarily the conversation's ``owner_user_id``. After PR's
    patient-access swap, a co-treating or successor clinician can
    resume a chat originally started by someone else; the context
    bundle, quota check, and metering all key off the resumer's
    identity so PHI travels with the patient, not the conversation
    owner.
    """

    conversation_id: str
    patient_id: str
    requesting_user_id: str
    caller_system_prompt: str
    caller_feature_key: str
    user_message: str
    source_selection: dict[str, object] | None
    model: str


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Per design doc §8: one retry on transient error with 1s backoff.
RETRYABLE_ERROR_CODES = frozenset({"timeout", "service_unavailable", "llm_error"})
RETRY_BACKOFF_SECONDS = 1.0
MAX_GATEWAY_ATTEMPTS = 2  # initial attempt + 1 retry on transient error

# Output-token cap per design doc §11.7. Downstream consumers can
# override per ``caller_feature_key`` once tier-aware quotas land in
# the ``LlmUsageMeter`` follow-up; for now this is a hard cap.
DEFAULT_MAX_OUTPUT_TOKENS = 2048

# Hard cap on assistant content length to stay inside the DB column
# check constraint (``ck_chat_messages_content_len`` enforces 32k).
MAX_CONTENT_CHARS = 32_000

# Windowed prior-turn budget. The conversation history fed to the model
# is the opening ``PRIOR_TURNS_HEAD`` messages (which frame the chat)
# plus the trailing ``PRIOR_TURNS_TAIL`` messages (the live dialogue);
# the middle is dropped. This keeps the per-turn read — and the prompt
# input — bounded instead of growing linearly with conversation length.
# ~15 recent exchanges (user + assistant) at the default tail.
PRIOR_TURNS_HEAD = 2
PRIOR_TURNS_TAIL = 30

# Inserted in place of the dropped middle so the model is told history
# was elided rather than silently contradicting an earlier decision.
ELIDED_HISTORY_MARKER = "[earlier turns omitted]"


# ---------------------------------------------------------------------------
# Errors surfaced upstream
# ---------------------------------------------------------------------------


class TurnConcurrencyError(Exception):
    """Raised when another turn is currently mid-flight for the same conversation.

    Design doc §14 specifies a row-level lock; the in-memory test repo
    serializes via the Python ``asyncio.Lock`` registry on the service
    instance. Either way, concurrent ``POST messages`` calls against
    the same conversation get a clean 409 instead of interleaved
    sequence numbers.
    """


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ChatTurnService:
    """Per-turn streaming workflow.

    One instance per request is fine — the service holds no per-user
    state. The conversation lock map is process-wide so concurrent
    requests against the same conversation serialize even when the
    Postgres ``next_sequence`` lock isn't available (e.g. the
    in-memory test repo).
    """

    _conversation_locks: ClassVar[dict[str, asyncio.Lock]] = {}

    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        notes_repo: NotesRepository,
        gateway: ChatLLMGateway,
        usage_meter: LlmUsageMeter | None = None,
        patient_documents_repo: PatientDocumentRepository | None = None,
        medication_repo: MedicationRepository | None = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._notes_repo = notes_repo
        self._gateway = gateway
        # Optional for in-memory tests that don't exercise the metering
        # path. The route always wires a real meter in; the absence of
        # one means metering is silently skipped.
        self._usage_meter = usage_meter
        # Optional for legacy tests whose fixtures pre-date the
        # ``patient_documents`` source. The route always wires a real
        # repo; selecting the source without one raises
        # :class:`InvalidSelectionError` inside the bundler.
        self._patient_documents_repo = patient_documents_repo
        # Optional for legacy tests. When supplied, active medication
        # rows are sourced from the structured medication table rather
        # than falling back to note-type scanning.
        self._medication_repo = medication_repo

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run_turn(self, context: TurnContext) -> AsyncIterator[TurnStreamEvent]:
        """Run one streaming turn. Yields :class:`TurnStreamEvent` items.

        Order: ``meta`` once, ``delta`` zero-or-more times, then exactly
        one ``done`` *or* ``error``. The route serializes events as
        SSE frames.
        """
        lock = self._conversation_locks.setdefault(context.conversation_id, asyncio.Lock())
        if lock.locked():
            raise TurnConcurrencyError(context.conversation_id)
        async with lock:
            async for event in self._run_turn_locked(context):
                yield event

    # ------------------------------------------------------------------
    # Core flow
    # ------------------------------------------------------------------

    async def _run_turn_locked(  # noqa: PLR0911,PLR0912,PLR0915 — streaming turn flow
        self, context: TurnContext
    ) -> AsyncIterator[TurnStreamEvent]:
        # Trim the user message to the column cap. Anything longer is
        # a client bug — surface it before persisting.
        user_text = (context.user_message or "").strip()
        if not user_text:
            yield _error_event(
                code="empty_message",
                message="Message content is required.",
            )
            return
        if len(user_text) > MAX_CONTENT_CHARS:
            yield _error_event(
                code="message_too_long",
                message=(f"Message exceeds {MAX_CONTENT_CHARS} characters."),
            )
            return

        # Quota gate (design doc §11.6). When enforcement is off (the
        # default) the meter returns OK and this is a no-op. Downstream
        # consumers subclass the meter to consult tenant config.
        quota_status: QuotaStatus = QuotaStatus.OK
        if self._usage_meter is not None:
            quota_status = self._usage_meter.check_quota(
                user_id=context.requesting_user_id,
                feature_key=context.caller_feature_key,
            )
            if quota_status == QuotaStatus.HARD_BLOCK:
                yield _error_event(
                    code="quota_exceeded",
                    message="LLM usage quota exceeded for this period.",
                )
                return

        # Assemble context. Pasted-text overflow is the only structural
        # failure the bundler raises; everything else is silently
        # truncated and reported in the manifest.
        selection = context.source_selection or default_source_selection()
        try:
            # Content-free retrieval span: records which chart documents fed
            # this turn (ids + token estimates only — never their text), as a
            # RETRIEVER span sitting beside the gateway's LLM span.
            with retrieval_span(operation="chat_context") as retrieval_rec:
                bundle: ContextBundle = assemble_context_bundle(
                    notes_repo=self._notes_repo,
                    patient_documents_repo=self._patient_documents_repo,
                    medication_repo=self._medication_repo,
                    patient_id=context.patient_id,
                    user_id=context.requesting_user_id,
                    selection=selection,
                    # Relevance-order patient documents against the turn's
                    # question so the most relevant docs survive truncation.
                    query=user_text,
                )
                retrieval_rec.set_documents(
                    [
                        RetrievedDocumentRef(
                            document_id=doc.document_id,
                            source=doc.source_key,
                            tokens_est=doc.tokens_est,
                        )
                        for doc in bundle.documents
                    ]
                )
                retrieval_rec.set_context_tokens(bundle.total_tokens_est)
        except ContextOverflowError as exc:
            yield _error_event(
                code="context_too_large",
                message=str(exc),
            )
            return
        except InvalidSelectionError as exc:
            yield _error_event(
                code="invalid_selection",
                message=str(exc),
            )
            return

        # Persist the user message. The sequence call locks the parent
        # row in Postgres (design doc §14), so the assistant row's
        # sequence is monotonic relative to this one.
        user_sequence = self._chat_repo.next_sequence(context.conversation_id)
        user_message = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=context.conversation_id,
            sequence=user_sequence,
            role="user",
            content=user_text,
            source_selection=dict(selection),
            context_manifest=bundle.manifest,
            created_at=utc_now(),
        )
        self._chat_repo.add_message(user_message)

        # Allocate the assistant row up front with empty content. We
        # update it in place at end-of-stream so a mid-stream crash
        # still leaves a forensic row recording what happened.
        assistant_sequence = self._chat_repo.next_sequence(context.conversation_id)
        assistant_message = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=context.conversation_id,
            sequence=assistant_sequence,
            role="assistant",
            content="",
            llm_model=context.model,
            created_at=utc_now(),
        )
        self._chat_repo.add_message(assistant_message)

        # Build prompt envelope (design doc §8).
        system_prompt = _compose_system_prompt(
            caller_system_prompt=context.caller_system_prompt,
            context_text=bundle.text,
        )
        prior_turns = self._load_prior_turns(
            context.conversation_id,
            user_id=context.requesting_user_id,
            exclude_message_ids={user_message.id, assistant_message.id},
        )
        input_tokens_estimate = _estimate_input_tokens(
            system_prompt=system_prompt,
            prior_turns=prior_turns,
            new_user_text=user_text,
        )
        assistant_message.input_tokens = input_tokens_estimate
        self._chat_repo.update_message(assistant_message)

        meta_data: dict[str, object] = {
            "user_message_id": user_message.id,
            "assistant_message_id": assistant_message.id,
            "input_tokens": input_tokens_estimate,
            "model": context.model,
            "manifest": bundle.manifest,
        }
        if quota_status == QuotaStatus.SOFT_WARN:
            # Hook for the UI to surface a "you're near your cap"
            # warning. The remaining-pct value comes from a downstream
            # quota config; the default implementation stays silent.
            meta_data["quota_status"] = QuotaStatus.SOFT_WARN.value
        yield TurnStreamEvent(kind="meta", data=meta_data)

        # Release the request-scoped pooled connection before the multi-
        # second gateway call. Holding it idle-in-transaction across the
        # LLM stream is what let a handful of concurrent chat turns drain
        # the pool (THERAPY-blx6 / THERAPY-vtrb). The user + assistant-
        # placeholder rows commit here; the post-stream update_message()
        # auto-begins a fresh transaction with search_path and the RLS
        # GUC re-armed by the checkout / after_begin listeners, so tenant
        # scoping survives transparently. Same seam the note-import and
        # SOAP-gen LLM paths already use. No-ops outside request scope
        # (unit tests with in-memory fakes, to_thread workers).
        from ..db import release_db_connection

        release_db_connection()

        # Stream from the gateway with one transient-error retry.
        attempt_buffers: list[str] = []
        final_event: StreamEvent | None = None
        attempts = 0
        retried = False
        while attempts < MAX_GATEWAY_ATTEMPTS:
            attempts += 1
            buffer: list[str] = []
            transient_failure = False
            async for event in self._gateway.stream_completion(
                model=context.model,
                system_prompt=system_prompt,
                prior_turns=prior_turns,
                new_user_text=user_text,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            ):
                if event.delta:
                    buffer.append(event.delta)
                    if not retried:
                        # On the first attempt, stream deltas directly
                        # to the client. On retry we suppress because
                        # the client already saw deltas (which we now
                        # need to send the *retry's* output instead);
                        # the simpler contract is to only stream the
                        # final attempt's text.
                        yield TurnStreamEvent(kind="delta", data={"text": event.delta})
                if event.finish_reason is not None:
                    final_event = event
                    if event.finish_reason == "error" and _is_retryable(event.error_code):
                        transient_failure = True
                    break
            attempt_buffers = buffer
            if not transient_failure:
                break
            # Discard the partial first-attempt buffer and try again.
            retried = True
            await asyncio.sleep(RETRY_BACKOFF_SECONDS)
            attempt_buffers = []

        # Persist final state on the assistant row.
        full_text = "".join(attempt_buffers)[:MAX_CONTENT_CHARS]
        assistant_message.content = full_text or "[no output]"
        assistant_message.output_tokens = final_event.output_tokens if final_event else None
        assistant_message.llm_finish_reason = final_event.finish_reason if final_event else "error"
        assistant_message.llm_error = (
            final_event.error_code if final_event and final_event.error_code else None
        )
        self._chat_repo.update_message(assistant_message)
        self._chat_repo.touch_last_turn_at(context.conversation_id, utc_now())

        # If we retried we never streamed the deltas live; replay them
        # now so the client sees the final assistant content.
        if retried and final_event and final_event.finish_reason != "error":
            for chunk in attempt_buffers:
                yield TurnStreamEvent(kind="delta", data={"text": chunk})

        if final_event is None:
            yield _error_event(
                code="llm_error",
                message="No completion received.",
            )
            return

        if final_event.finish_reason == "safety":
            yield _error_event(
                code="safety_block",
                message="Response was blocked by safety filters.",
            )
            return
        if final_event.finish_reason == "error":
            yield _error_event(
                code=final_event.error_code or "llm_error",
                message=final_event.error_message or "LLM error",
            )
            return

        # Meter the completed turn (design doc §11.6). Only successful
        # turns count — safety blocks, transient errors, and missing
        # completions are already short-circuited above. Failures
        # inside the meter are swallowed there; this call is best-
        # effort and must not affect the client-visible stream.
        if self._usage_meter is not None:
            self._usage_meter.record_turn(
                user_id=context.requesting_user_id,
                feature_key=context.caller_feature_key,
                model=context.model,
                input_tokens=assistant_message.input_tokens or 0,
                output_tokens=assistant_message.output_tokens or 0,
            )

        # Instrumentation hook (no-op in the base service). Hands a subclass
        # the exact envelope the model received — composed system prompt,
        # retrieved documents, prior turns, reply — for optional capture. Any
        # error here is swallowed: instrumentation must never break the turn.
        try:
            self._record_turn_content(
                context=context,
                bundle=bundle,
                system_prompt=system_prompt,
                prior_turns=prior_turns,
                assistant_text=full_text,
                input_tokens=assistant_message.input_tokens,
                output_tokens=assistant_message.output_tokens,
            )
        except Exception:
            logger.exception(
                "turn content hook raised for conversation %s (turn delivery unaffected)",
                context.conversation_id,
            )

        yield TurnStreamEvent(
            kind="done",
            data={
                "output_tokens": assistant_message.output_tokens,
                "finish_reason": final_event.finish_reason,
            },
        )

    # ------------------------------------------------------------------
    # Instrumentation hook
    # ------------------------------------------------------------------

    def _record_turn_content(
        self,
        *,
        context: TurnContext,
        bundle: ContextBundle,
        system_prompt: str,
        prior_turns: list[UserAssistantTurn],
        assistant_text: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        """Hook fired once per successful turn — no-op in the base service.

        Receives the exact prompt envelope the model saw (the composed
        ``system_prompt``, the structured retrieved ``bundle.documents``,
        the ``prior_turns``, and the assistant reply) so an instrumentation
        subclass can record it — e.g. attach redacted content to the
        retrieval/LLM spans for quality review or eval capture.

        The base implementation deliberately does nothing: by default
        chart content stays out of telemetry. Overrides run on the request
        event loop while the DB session is live, must not block (schedule
        their own background work), and must not raise — the caller swallows
        exceptions, but an override should still treat this as best-effort.
        """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_prior_turns(
        self,
        conversation_id: str,
        *,
        user_id: str,
        exclude_message_ids: set[str],
    ) -> list[UserAssistantTurn]:
        # Windowed read: the opening turn + the most-recent turns, not the
        # whole conversation. The two just-created rows for this turn (the
        # user message and the empty assistant placeholder) sit at the tail
        # and are filtered out via ``exclude_message_ids``.
        messages = self._chat_repo.list_messages_windowed(
            conversation_id, user_id, head=PRIOR_TURNS_HEAD, tail=PRIOR_TURNS_TAIL
        )
        # When the window stitched a head slice to a tail slice across a
        # gap, mark the head-side sequence so we can splice in an elision
        # marker once — telling the model history was dropped.
        elide_after_seq = _first_window_gap_sequence(messages)
        marker_inserted = False
        prior: list[UserAssistantTurn] = []
        for msg in messages:
            if msg.id in exclude_message_ids:
                continue
            if msg.role == "user":
                role: Literal["user", "assistant"] = "user"
            elif msg.role == "assistant":
                role = "assistant"
            else:
                continue
            if not msg.content:
                continue
            if (
                elide_after_seq is not None
                and not marker_inserted
                and msg.sequence > elide_after_seq
            ):
                prior.append(
                    UserAssistantTurn(role="assistant", content=ELIDED_HISTORY_MARKER)
                )
                marker_inserted = True
            prior.append(UserAssistantTurn(role=role, content=msg.content))
        return prior


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _first_window_gap_sequence(messages: list[ChatMessage]) -> int | None:
    """Sequence after which the windowed history skips ≥1 turn.

    :meth:`ChatRepository.list_messages_windowed` stitches a head slice to
    a tail slice; sequences are otherwise contiguous, so a single
    discontinuity marks the boundary where the middle was dropped. Returns
    the head-side sequence at that boundary, or ``None`` when the window is
    contiguous (nothing was elided).
    """
    for prev, cur in itertools.pairwise(messages):
        if cur.sequence > prev.sequence + 1:
            return prev.sequence
    return None


def _error_event(*, code: str, message: str) -> TurnStreamEvent:
    return TurnStreamEvent(
        kind="error",
        data={"error": code, "message": message},
    )


def _is_retryable(error_code: str | None) -> bool:
    return error_code is not None and error_code in RETRYABLE_ERROR_CODES


_EMPTY_CHART_MARKER = (
    "(No chart data is available for any of the selected sources. "
    "Do not infer or invent patient details such as demographics, "
    "diagnoses, medications, history, or session content. If the user "
    "asks about the patient, state explicitly that the chart contains "
    "no information for those sections.)"
)


def _compose_system_prompt(
    *,
    caller_system_prompt: str,
    context_text: str,
) -> str:
    """Concatenate the caller prompt + context block per design doc §8.

    When ``context_text`` is empty — the bundler returned no data for
    any selected source (e.g., a brand-new patient with no notes,
    intake, treatment plan, meds, or documents) — emit an explicit
    empty-chart marker block so the model has a positive signal that
    the chart contains no data. Without this, the model receives only
    the caller prompt and a question, and defaults to "being helpful"
    by confabulating a plausible patient from training-data priors.
    See pablo-saas ``THERAPY-fr6y`` for the prod failure that motivated
    this change. Regression-tested by the chat-hallu-005 case in
    ``backend/evals/datasets/chat.yaml``.
    """
    parts = [caller_system_prompt.strip(), "\n\n--- PATIENT CONTEXT ---\n"]
    parts.append(context_text if context_text else _EMPTY_CHART_MARKER)
    return "".join(parts)


def _estimate_input_tokens(
    *,
    system_prompt: str,
    prior_turns: list[UserAssistantTurn],
    new_user_text: str,
) -> int:
    """Cheap char-based estimate. Phase-3 turn service uses this for the
    meta event and the persisted ``input_tokens`` column; the gateway
    response is authoritative for billing once it lands."""
    char_total = len(system_prompt) + len(new_user_text)
    for turn in prior_turns:
        char_total += len(turn.content)
    # Match the bundler's chars-per-token heuristic so totals reconcile.
    chars_per_token = 4
    return (char_total + chars_per_token - 1) // chars_per_token


__all__ = [
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MAX_CONTENT_CHARS",
    "RETRYABLE_ERROR_CODES",
    "RETRY_BACKOFF_SECONDS",
    "ChatTurnService",
    "TurnConcurrencyError",
    "TurnContext",
    "TurnStreamEvent",
]
