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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ..repositories import ChatRepository, NotesRepository
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
    route to have run the patient ACL + owner check.
    """

    conversation_id: str
    patient_id: str
    owner_user_id: str
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

# Output-token cap per design doc §11.7. The SaaS overlay can override
# per ``caller_feature_key`` once tier-aware quotas land in the
# ``LlmUsageMeter`` follow-up; for now this is a hard cap.
DEFAULT_MAX_OUTPUT_TOKENS = 2048

# Hard cap on assistant content length to stay inside the DB column
# check constraint (``ck_chat_messages_content_len`` enforces 32k).
MAX_CONTENT_CHARS = 32_000


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
    ) -> None:
        self._chat_repo = chat_repo
        self._notes_repo = notes_repo
        self._gateway = gateway
        # Optional for in-memory tests that don't exercise the metering
        # path. The route always wires a real meter in; the absence of
        # one means metering is silently skipped.
        self._usage_meter = usage_meter

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

        # Quota gate (design doc §11.6). When enforcement is off (OSS
        # default) the meter returns OK and this is a no-op. SaaS
        # overlays subclass the meter to consult tenant config.
        quota_status: QuotaStatus = QuotaStatus.OK
        if self._usage_meter is not None:
            quota_status = self._usage_meter.check_quota(
                user_id=context.owner_user_id,
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
            bundle: ContextBundle = assemble_context_bundle(
                notes_repo=self._notes_repo,
                patient_id=context.patient_id,
                selection=selection,
            )
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
            # warning. The remaining-pct value comes from the SaaS
            # overlay's quota config; OSS stays silent.
            meta_data["quota_status"] = QuotaStatus.SOFT_WARN.value
        yield TurnStreamEvent(kind="meta", data=meta_data)

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
                user_id=context.owner_user_id,
                feature_key=context.caller_feature_key,
                model=context.model,
                input_tokens=assistant_message.input_tokens or 0,
                output_tokens=assistant_message.output_tokens or 0,
            )

        yield TurnStreamEvent(
            kind="done",
            data={
                "output_tokens": assistant_message.output_tokens,
                "finish_reason": final_event.finish_reason,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_prior_turns(
        self, conversation_id: str, *, exclude_message_ids: set[str]
    ) -> list[UserAssistantTurn]:
        messages = self._chat_repo.list_messages(conversation_id)
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
            prior.append(UserAssistantTurn(role=role, content=msg.content))
        return prior


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _error_event(*, code: str, message: str) -> TurnStreamEvent:
    return TurnStreamEvent(
        kind="error",
        data={"error": code, "message": message},
    )


def _is_retryable(error_code: str | None) -> bool:
    return error_code is not None and error_code in RETRYABLE_ERROR_CODES


def _compose_system_prompt(
    *,
    caller_system_prompt: str,
    context_text: str,
) -> str:
    """Concatenate the caller prompt + context block per design doc §8."""
    parts = [caller_system_prompt.strip()]
    if context_text:
        parts.append("\n\n--- PATIENT CONTEXT ---\n")
        parts.append(context_text)
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
