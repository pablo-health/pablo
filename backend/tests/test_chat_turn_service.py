# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the chat turn service (THERAPY-5x5, Phase 3 of THERAPY-bhv).

Covers prompt-envelope construction, persistence (user row → assistant
row → token counts), retry on transient gateway error, safety-block
short-circuit, error-finish-reason propagation, and per-conversation
concurrency.

OSS test infrastructure does not ship pytest-asyncio. Each test wraps
its async body with ``asyncio.run`` rather than depending on a plugin
or polluting ``pytest.ini`` with a new marker.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from app.models import ChatConversation, ChatMessage, QuotaStatus
from app.repositories import (
    InMemoryChatRepository,
    InMemoryLlmUsageRepository,
    InMemoryNotesRepository,
)
from app.services import LlmUsageMeter
from app.services.chat_context_bundler import SOURCE_KEY_PASTED_TEXT, ContextBundle
from app.services.chat_llm_gateway import (
    ChatLLMGateway,
    FakeChatLLMGateway,
    StreamEvent,
    UserAssistantTurn,
)
from app.services.chat_turn_service import (
    ELIDED_HISTORY_MARKER,
    PRIOR_TURNS_HEAD,
    PRIOR_TURNS_TAIL,
    ChatTurnService,
    TurnConcurrencyError,
    TurnContext,
    _compose_system_prompt,
    _first_window_gap_sequence,
    _StreamOutcome,
)
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

CONVERSATION_ID = "conv-turn-1"
PATIENT_ID = "patient-turn-1"
OWNER_USER_ID = "user-turn-1"


async def _no_sleep(_seconds: float) -> None:
    """Retry-backoff stand-in so retry tests don't sleep for real."""


def _make_conversation() -> ChatConversation:
    return ChatConversation(
        id=CONVERSATION_ID,
        patient_id=PATIENT_ID,
        owner_user_id=OWNER_USER_ID,
        title="Sleep history",
        caller_system_prompt="You are a clinical assistant.",
        caller_feature_key="chart_qa",
        created_at=datetime.now(UTC),
    )


def _make_context(*, message: str = "What have we tried for sleep?") -> TurnContext:
    return TurnContext(
        conversation_id=CONVERSATION_ID,
        patient_id=PATIENT_ID,
        requesting_user_id=OWNER_USER_ID,
        caller_system_prompt="You are a clinical assistant.",
        caller_feature_key="chart_qa",
        user_message=message,
        source_selection=None,
        model="gemini-test-flash",
    )


@pytest.fixture
def chat_repo() -> InMemoryChatRepository:
    # Grant universal access — the turn-service tests focus on the
    # streaming workflow, not the cross-clinician access boundary. The
    # IDOR tests in test_routes_chat.py exercise the boundary
    # explicitly.
    repo = InMemoryChatRepository()
    repo.grant_all_access()
    repo.add_conversation(_make_conversation(), OWNER_USER_ID)
    return repo


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    repo = InMemoryNotesRepository()
    repo.grant_all_access()
    return repo


@pytest.fixture(autouse=True)
def reset_concurrency_locks() -> None:
    ChatTurnService._conversation_locks.clear()


class _MeterSettings:
    """Minimal Settings stand-in for tests that exercise the meter."""

    def __init__(self, *, llm_quota_enforcement: str = "off") -> None:
        self.llm_quota_enforcement = llm_quota_enforcement


def _make_service(
    chat_repo: InMemoryChatRepository,
    notes_repo: InMemoryNotesRepository,
    *,
    script: list[StreamEvent] | None = None,
    scripts: list[list[StreamEvent]] | None = None,
    usage_meter: LlmUsageMeter | None = None,
) -> tuple[ChatTurnService, FakeChatLLMGateway]:
    gateway = FakeChatLLMGateway(
        script=list(script or []),
        scripts=[list(s) for s in (scripts or [])],
    )
    service = ChatTurnService(
        chat_repo=chat_repo,
        notes_repo=notes_repo,
        gateway=gateway,
        usage_meter=usage_meter,
    )
    return service, gateway


def _make_meter(
    *,
    enforcement: str = "off",
    quota_override: QuotaStatus | None = None,
) -> tuple[LlmUsageMeter, InMemoryLlmUsageRepository]:
    repo = InMemoryLlmUsageRepository()
    meter = LlmUsageMeter(repo=repo, settings=_MeterSettings(llm_quota_enforcement=enforcement))
    if quota_override is not None:
        # Pin ``check_quota`` for tests that need a deterministic
        # SOFT_WARN / HARD_BLOCK outcome — the OSS default always
        # returns OK, but the integration with the turn service is
        # the same regardless of which subclass produced the value.
        meter.check_quota = lambda **_kwargs: quota_override  # type: ignore[method-assign]
    return meter, repo


def _drain(service: ChatTurnService, context: TurnContext) -> list:
    async def _impl() -> list:
        return [e async for e in service.run_turn(context)]

    return asyncio.run(_impl())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_streams_deltas_then_done(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="Hello "),
                StreamEvent(delta="world."),
                StreamEvent(finish_reason="stop", output_tokens=42),
            ],
        )
        events = _drain(service, _make_context())

        kinds = [e.kind for e in events]
        assert kinds[0] == "meta"
        assert kinds[-1] == "done"
        assert kinds.count("delta") == 2
        delta_text = "".join(str(e.data["text"]) for e in events if e.kind == "delta")
        assert delta_text == "Hello world."
        done = events[-1]
        assert done.data["finish_reason"] == "stop"
        assert done.data["output_tokens"] == 42

    def test_persists_user_and_assistant_rows(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="A "),
                StreamEvent(delta="reply."),
                StreamEvent(finish_reason="stop", output_tokens=10),
            ],
        )
        _drain(service, _make_context(message="Question?"))

        messages = chat_repo.list_messages(CONVERSATION_ID)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert [m.sequence for m in messages] == [1, 2]
        assert messages[0].content == "Question?"
        assert messages[1].content == "A reply."
        assert messages[1].output_tokens == 10
        assert messages[1].llm_model == "gemini-test-flash"
        assert messages[1].llm_finish_reason == "stop"

    def test_bumps_last_turn_at(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="ok"),
                StreamEvent(finish_reason="stop", output_tokens=1),
            ],
        )
        _drain(service, _make_context())
        conv = chat_repo.get_conversation(CONVERSATION_ID)
        assert conv is not None
        assert conv.last_turn_at is not None

    def test_prompt_envelope_includes_system_prompt(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="ok"),
                StreamEvent(finish_reason="stop", output_tokens=1),
            ],
        )
        _drain(service, _make_context(message="anything"))
        assert len(gateway.calls) == 1
        call = gateway.calls[0]
        assert call["model"] == "gemini-test-flash"
        assert call["system_prompt"].startswith("You are a clinical assistant.")
        assert call["new_user_text"] == "anything"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_safety_block_emits_error(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(finish_reason="safety"),
            ],
        )
        events = _drain(service, _make_context())
        assert events[0].kind == "meta"
        assert events[-1].kind == "error"
        assert events[-1].data["error"] == "safety_block"

    def test_safety_block_records_finish_reason(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="partial..."),
                StreamEvent(finish_reason="safety"),
            ],
        )
        _drain(service, _make_context())
        messages = chat_repo.list_messages(CONVERSATION_ID)
        assistant = messages[-1]
        assert assistant.llm_finish_reason == "safety"

    def test_retries_once_on_transient_then_succeeds(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.chat_turn_service._retry_sleep", _no_sleep)
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            scripts=[
                [StreamEvent(finish_reason="error", error_code="service_unavailable")],
                [
                    StreamEvent(delta="recovered"),
                    StreamEvent(finish_reason="stop", output_tokens=3),
                ],
            ],
        )
        events = _drain(service, _make_context())
        assert events[-1].kind == "done"
        assert len(gateway.calls) == 2
        messages = chat_repo.list_messages(CONVERSATION_ID)
        assert messages[-1].content == "recovered"

    def test_no_retry_on_safety_block(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(finish_reason="safety"),
            ],
        )
        _drain(service, _make_context())
        assert len(gateway.calls) == 1

    def test_second_transient_failure_surfaces_error(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.chat_turn_service._retry_sleep", _no_sleep)
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            scripts=[
                [StreamEvent(finish_reason="error", error_code="timeout")],
                [StreamEvent(finish_reason="error", error_code="timeout")],
            ],
        )
        events = _drain(service, _make_context())
        assert events[-1].kind == "error"
        assert events[-1].data["error"] == "timeout"
        assert len(gateway.calls) == 2

    def test_empty_message_short_circuits(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="never reached"),
                StreamEvent(finish_reason="stop", output_tokens=1),
            ],
        )
        events = _drain(service, _make_context(message="   "))
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].data["error"] == "empty_message"
        assert chat_repo.list_messages(CONVERSATION_ID) == []
        assert gateway.calls == []


class _HangingChatGateway(ChatLLMGateway):
    """Yields one delta, then hangs until cancelled.

    Models a client disconnect mid-stream: the gateway's underlying
    call never resolves on its own, so the only thing that can end it
    is the caller closing the generator (which must cancel whatever is
    still consuming this stream).
    """

    async def stream_completion(
        self,
        *,
        model: str,
        system_prompt: str,
        prior_turns: list[UserAssistantTurn],
        new_user_text: str,
        max_output_tokens: int,
        temperature: float = 0.4,
    ):
        yield StreamEvent(delta="first")
        await asyncio.Event().wait()
        yield StreamEvent(finish_reason="stop")  # pragma: no cover — unreachable


class TestClientDisconnect:
    def test_stream_with_retry_cancels_drive_task_on_early_close(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        """Regression: closing the turn generator early (a client
        disconnect) must cancel the in-flight retry-drive task rather
        than leave it — and the gateway stream it's still consuming —
        running server-side with nothing left to read its output.
        """
        service = ChatTurnService(
            chat_repo=chat_repo,
            notes_repo=notes_repo,
            gateway=_HangingChatGateway(),
        )

        async def _impl() -> None:
            outcome = _StreamOutcome()
            before = asyncio.all_tasks()
            gen = service._stream_with_retry(
                _make_context(),
                system_prompt="sys",
                prior_turns=[],
                user_text="hi",
                outcome=outcome,
            )
            first = await gen.__anext__()
            assert first.data["text"] == "first"

            new_tasks = asyncio.all_tasks() - before
            assert len(new_tasks) == 1
            drive_task = next(iter(new_tasks))
            assert not drive_task.done()

            await gen.aclose()
            await asyncio.sleep(0)  # let the cancellation fully land

            assert drive_task.cancelled()

        asyncio.run(_impl())


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_turn_raises(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        # Build a gateway that holds the stream open until released so
        # the first turn is still in-flight when the second arrives.
        gate = asyncio.Event()

        class _BlockingGateway(FakeChatLLMGateway):
            async def stream_completion(self, **kwargs):  # type: ignore[no-untyped-def,override]
                self.calls.append(dict(kwargs))
                await gate.wait()
                yield StreamEvent(delta="late")
                yield StreamEvent(finish_reason="stop", output_tokens=1)

        gateway = _BlockingGateway()
        service = ChatTurnService(chat_repo=chat_repo, notes_repo=notes_repo, gateway=gateway)

        async def _impl() -> list:
            async def _consume() -> list:
                return [e async for e in service.run_turn(_make_context())]

            first = asyncio.create_task(_consume())
            await asyncio.sleep(0.01)
            with pytest.raises(TurnConcurrencyError):
                async for _ in service.run_turn(_make_context()):
                    pass
            gate.set()
            return await first

        result = asyncio.run(_impl())
        assert result[-1].kind == "done"


# ---------------------------------------------------------------------------
# Metering + quota integration (THERAPY-f6eg, Phase 3b)
# ---------------------------------------------------------------------------


class TestMetering:
    def test_successful_turn_records_one_meter_row(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        meter, repo = _make_meter()
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="ok"),
                StreamEvent(finish_reason="stop", output_tokens=7),
            ],
            usage_meter=meter,
        )
        _drain(service, _make_context())
        records = [
            r
            for period in {f"{datetime.now(UTC).year:04d}{datetime.now(UTC).month:02d}"}
            for r in repo.list_records(period_yyyymm=period)
        ]
        assert len(records) == 1
        row = records[0]
        assert row.user_id == OWNER_USER_ID
        assert row.feature_key == "chart_qa"
        assert row.model == "gemini-test-flash"
        assert row.turn_count == 1
        assert row.output_tokens == 7

    def test_safety_block_does_not_record(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        meter, repo = _make_meter()
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[StreamEvent(finish_reason="safety")],
            usage_meter=meter,
        )
        _drain(service, _make_context())
        period = f"{datetime.now(UTC).year:04d}{datetime.now(UTC).month:02d}"
        assert repo.list_records(period_yyyymm=period) == []

    def test_retry_then_success_records_once(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.chat_turn_service._retry_sleep", _no_sleep)
        meter, repo = _make_meter()
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            scripts=[
                [StreamEvent(finish_reason="error", error_code="timeout")],
                [
                    StreamEvent(delta="retried."),
                    StreamEvent(finish_reason="stop", output_tokens=4),
                ],
            ],
            usage_meter=meter,
        )
        _drain(service, _make_context())
        period = f"{datetime.now(UTC).year:04d}{datetime.now(UTC).month:02d}"
        rows = repo.list_records(period_yyyymm=period)
        assert len(rows) == 1
        assert rows[0].turn_count == 1
        assert rows[0].output_tokens == 4

    def test_hard_block_short_circuits_before_persistence(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        meter, repo = _make_meter(quota_override=QuotaStatus.HARD_BLOCK)
        service, gateway = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="never reached"),
                StreamEvent(finish_reason="stop", output_tokens=1),
            ],
            usage_meter=meter,
        )
        events = _drain(service, _make_context())
        assert len(events) == 1
        assert events[0].kind == "error"
        assert events[0].data["error"] == "quota_exceeded"
        # No user/assistant rows persisted, no gateway call made, no
        # metering row written for the rejected turn.
        assert chat_repo.list_messages(CONVERSATION_ID) == []
        assert gateway.calls == []
        period = f"{datetime.now(UTC).year:04d}{datetime.now(UTC).month:02d}"
        assert repo.list_records(period_yyyymm=period) == []

    def test_soft_warn_enriches_meta_and_records(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        meter, repo = _make_meter(quota_override=QuotaStatus.SOFT_WARN)
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[
                StreamEvent(delta="ok"),
                StreamEvent(finish_reason="stop", output_tokens=3),
            ],
            usage_meter=meter,
        )
        events = _drain(service, _make_context())
        meta = events[0]
        assert meta.kind == "meta"
        assert meta.data.get("quota_status") == "soft_warn"
        period = f"{datetime.now(UTC).year:04d}{datetime.now(UTC).month:02d}"
        assert len(repo.list_records(period_yyyymm=period)) == 1


# ---------------------------------------------------------------------------
# Prompt composition — empty-chart marker
# ---------------------------------------------------------------------------


class TestComposeSystemPromptEmptyChartMarker:
    """When the bundler returns no data, _compose_system_prompt must still
    emit a PATIENT CONTEXT block with an explicit empty-chart marker.

    Without this, the model receives only the caller prompt and a
    question, and confabulates a plausible patient from training-data
    priors — see the downstream production incident where a fresh
    patient chart produced a fabricated 45-year-old depression
    exemplar instead of a refusal.
    """

    _CALLER_PROMPT = "You are Pablo, a clinical chat assistant."

    def test_empty_context_emits_marker_block(self) -> None:
        result = _compose_system_prompt(
            caller_system_prompt=self._CALLER_PROMPT,
            context_text="",
        )
        assert "PATIENT CONTEXT" in result, (
            "PATIENT CONTEXT block must be present even when context_text is empty"
        )

    def test_empty_context_marker_signals_no_data(self) -> None:
        result = _compose_system_prompt(
            caller_system_prompt=self._CALLER_PROMPT,
            context_text="",
        ).lower()
        # Any of these phrases satisfies the "model knows chart is empty"
        # signal. Keeping the assertion broad so a future copy edit
        # doesn't break the test for the wrong reason.
        assert any(
            phrase in result
            for phrase in (
                "no chart data",
                "no data is available",
                "chart contains no information",
            )
        ), "Empty-chart marker must explicitly signal that the chart has no data"

    def test_empty_context_marker_forbids_invention(self) -> None:
        """Marker must instruct the model not to fabricate details."""
        result = _compose_system_prompt(
            caller_system_prompt=self._CALLER_PROMPT,
            context_text="",
        ).lower()
        assert "do not infer" in result or "do not invent" in result, (
            "Empty-chart marker must instruct the model not to invent patient details"
        )

    def test_non_empty_context_passes_through_unchanged(self) -> None:
        """When real context is present, no empty-chart marker should appear."""
        real_context = "Patient: Test Patient (DOB 1900-01-01)\nMost recent session: ..."
        result = _compose_system_prompt(
            caller_system_prompt=self._CALLER_PROMPT,
            context_text=real_context,
        )
        assert "PATIENT CONTEXT" in result
        assert real_context in result
        assert "no chart data" not in result.lower(), (
            "Empty-chart marker should NOT appear when real context is present"
        )

    def test_caller_prompt_is_stripped_and_preserved(self) -> None:
        """Leading/trailing whitespace on the caller prompt should be trimmed."""
        result = _compose_system_prompt(
            caller_system_prompt="\n\n  You are Pablo.  \n\n",
            context_text="",
        )
        assert result.startswith("You are Pablo.")
        # And the marker still follows
        assert "PATIENT CONTEXT" in result


# ---------------------------------------------------------------------------
# Retrieval span + content-capture hook
# ---------------------------------------------------------------------------


@pytest.fixture
def span_exporter():
    """In-memory span exporter on the active provider (mirrors test_llm_telemetry)."""
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    yield exporter
    exporter.clear()


def _pasted_context(content: str = "External sleep log entry.") -> TurnContext:
    return TurnContext(
        conversation_id=CONVERSATION_ID,
        patient_id=PATIENT_ID,
        requesting_user_id=OWNER_USER_ID,
        caller_system_prompt="You are a clinical assistant.",
        caller_feature_key="chart_qa",
        user_message="What does the pasted log show?",
        source_selection={SOURCE_KEY_PASTED_TEXT: {"content": content}},
        model="gemini-test-flash",
    )


class TestRetrievalSpanAndContentHook:
    def test_turn_emits_content_free_retrieval_span(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        service, _ = _make_service(
            chat_repo,
            notes_repo,
            script=[StreamEvent(delta="ok"), StreamEvent(finish_reason="stop", output_tokens=3)],
        )
        _drain(service, _pasted_context())

        retrieval = [
            s for s in span_exporter.get_finished_spans() if s.name == "retrieval.chat_context"
        ]
        assert len(retrieval) == 1
        attrs = dict(retrieval[0].attributes or {})
        assert (
            attrs[SpanAttributes.OPENINFERENCE_SPAN_KIND]
            == OpenInferenceSpanKindValues.RETRIEVER.value
        )
        assert attrs["pablo.retrieval.document_count"] >= 1
        assert (
            attrs[f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.0.document.id"] == SOURCE_KEY_PASTED_TEXT
        )
        # Content-free: no document text, and the pasted content never appears.
        assert not any(key.endswith(".document.content") for key in attrs)
        haystack = " ".join(f"{k}={v}" for k, v in attrs.items())
        assert "External sleep log entry." not in haystack

    def test_record_turn_content_hook_receives_envelope(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        captured: dict = {}

        class _CapturingService(ChatTurnService):
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
                captured.update(
                    bundle=bundle,
                    system_prompt=system_prompt,
                    prior_turns=prior_turns,
                    assistant_text=assistant_text,
                    output_tokens=output_tokens,
                )

        gateway = FakeChatLLMGateway(
            script=[
                StreamEvent(delta="Hi "),
                StreamEvent(delta="there."),
                StreamEvent(finish_reason="stop", output_tokens=5),
            ]
        )
        service = _CapturingService(chat_repo=chat_repo, notes_repo=notes_repo, gateway=gateway)
        _drain(service, _pasted_context())

        assert captured["assistant_text"] == "Hi there."
        assert "You are a clinical assistant." in captured["system_prompt"]
        assert isinstance(captured["bundle"], ContextBundle)
        # The structured per-document content is handed to the hook (the seam
        # that lets the overlay capture per-document content for evals).
        assert any(d.source_key == SOURCE_KEY_PASTED_TEXT for d in captured["bundle"].documents)
        assert all(isinstance(t, UserAssistantTurn) for t in captured["prior_turns"])
        assert captured["output_tokens"] == 5

    def test_hook_exception_does_not_break_the_turn(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        class _BoomService(ChatTurnService):
            def _record_turn_content(self, **kwargs: object) -> None:
                raise RuntimeError("hook boom")

        gateway = FakeChatLLMGateway(
            script=[StreamEvent(delta="ok"), StreamEvent(finish_reason="stop", output_tokens=2)]
        )
        service = _BoomService(chat_repo=chat_repo, notes_repo=notes_repo, gateway=gateway)
        events = _drain(service, _pasted_context())
        # The turn still completes — a misbehaving hook never breaks delivery.
        assert events[-1].kind == "done"


# ---------------------------------------------------------------------------
# Prior-turn window (_load_prior_turns / _first_window_gap_sequence)
# ---------------------------------------------------------------------------


def _seed_message(chat_repo: InMemoryChatRepository, *, role: str, content: str) -> ChatMessage:
    return chat_repo.add_message(
        ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=CONVERSATION_ID,
            sequence=chat_repo.next_sequence(CONVERSATION_ID),
            role=role,
            content=content,
            created_at=datetime.now(UTC),
        )
    )


def _seed_turns(chat_repo: InMemoryChatRepository, count: int) -> None:
    """Seed ``count`` alternating user/assistant rows with distinct content."""
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        _seed_message(chat_repo, role=role, content=f"turn {i}")


def _message_at(seq: int) -> ChatMessage:
    return ChatMessage(
        id=str(uuid.uuid4()),
        conversation_id=CONVERSATION_ID,
        sequence=seq,
        role="user",
        content="x",
        created_at=datetime.now(UTC),
    )


class TestPriorTurnWindow:
    def test_short_conversation_returns_every_turn_with_no_marker(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(chat_repo, notes_repo)
        _seed_turns(chat_repo, PRIOR_TURNS_HEAD + 2)

        prior = service._load_prior_turns(
            CONVERSATION_ID, user_id=OWNER_USER_ID, exclude_message_ids=set()
        )

        assert len(prior) == PRIOR_TURNS_HEAD + 2
        assert all(turn.content != ELIDED_HISTORY_MARKER for turn in prior)

    def test_long_conversation_windows_to_head_plus_tail_with_one_marker(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(chat_repo, notes_repo)
        _seed_turns(chat_repo, PRIOR_TURNS_HEAD + PRIOR_TURNS_TAIL + 10)

        prior = service._load_prior_turns(
            CONVERSATION_ID, user_id=OWNER_USER_ID, exclude_message_ids=set()
        )

        assert len(prior) == PRIOR_TURNS_HEAD + PRIOR_TURNS_TAIL + 1
        marker_positions = [
            i for i, turn in enumerate(prior) if turn.content == ELIDED_HISTORY_MARKER
        ]
        assert marker_positions == [PRIOR_TURNS_HEAD]

    def test_current_turn_rows_excluded_via_exclude_message_ids(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(chat_repo, notes_repo)
        _seed_turns(chat_repo, PRIOR_TURNS_HEAD)
        current_user_msg = _seed_message(chat_repo, role="user", content="current question")
        current_assistant_msg = _seed_message(chat_repo, role="assistant", content="")

        prior = service._load_prior_turns(
            CONVERSATION_ID,
            user_id=OWNER_USER_ID,
            exclude_message_ids={current_user_msg.id, current_assistant_msg.id},
        )

        assert len(prior) == PRIOR_TURNS_HEAD
        assert all(turn.content != "current question" for turn in prior)

    def test_empty_content_and_non_dialogue_rows_are_skipped(
        self,
        chat_repo: InMemoryChatRepository,
        notes_repo: InMemoryNotesRepository,
    ) -> None:
        service, _ = _make_service(chat_repo, notes_repo)
        _seed_message(chat_repo, role="user", content="hello")
        _seed_message(chat_repo, role="assistant", content="")
        _seed_message(chat_repo, role="system", content="tool ran")
        _seed_message(chat_repo, role="assistant", content="hi back")

        prior = service._load_prior_turns(
            CONVERSATION_ID, user_id=OWNER_USER_ID, exclude_message_ids=set()
        )

        assert [turn.content for turn in prior] == ["hello", "hi back"]


class TestFirstWindowGapSequence:
    def test_contiguous_sequence_has_no_gap(self) -> None:
        messages = [_message_at(seq) for seq in range(1, 5)]
        assert _first_window_gap_sequence(messages) is None

    def test_gap_returns_head_side_boundary_sequence(self) -> None:
        messages = [_message_at(1), _message_at(2), _message_at(40), _message_at(41)]
        assert _first_window_gap_sequence(messages) == 2

    def test_empty_and_single_message_lists_have_no_gap(self) -> None:
        assert _first_window_gap_sequence([]) is None
        assert _first_window_gap_sequence([_message_at(1)]) is None
