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
from datetime import UTC, datetime

import pytest
from app.models import ChatConversation, QuotaStatus
from app.repositories import (
    InMemoryChatRepository,
    InMemoryLlmUsageRepository,
    InMemoryNotesRepository,
)
from app.services import LlmUsageMeter
from app.services.chat_llm_gateway import (
    FakeChatLLMGateway,
    StreamEvent,
)
from app.services.chat_turn_service import (
    ChatTurnService,
    TurnConcurrencyError,
    TurnContext,
)

CONVERSATION_ID = "conv-turn-1"
PATIENT_ID = "patient-turn-1"
OWNER_USER_ID = "user-turn-1"


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
        owner_user_id=OWNER_USER_ID,
        caller_system_prompt="You are a clinical assistant.",
        caller_feature_key="chart_qa",
        user_message=message,
        source_selection=None,
        model="gemini-test-flash",
    )


@pytest.fixture
def chat_repo() -> InMemoryChatRepository:
    repo = InMemoryChatRepository()
    repo.add_conversation(_make_conversation())
    return repo


@pytest.fixture
def notes_repo() -> InMemoryNotesRepository:
    return InMemoryNotesRepository()


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
        monkeypatch.setattr("app.services.chat_turn_service.RETRY_BACKOFF_SECONDS", 0)
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
        monkeypatch.setattr("app.services.chat_turn_service.RETRY_BACKOFF_SECONDS", 0)
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
        monkeypatch.setattr("app.services.chat_turn_service.RETRY_BACKOFF_SECONDS", 0)
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
