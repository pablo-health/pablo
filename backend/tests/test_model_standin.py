# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Model calls of listed practices go to the stand-in; everyone else's do not.

``MODEL_STANDIN_PRACTICES`` names practices and ``MODEL_STANDIN_BASE_URL`` the
stand-in that answers their structured calls, chat answers and dictated clips.
Practice A is listed and practice B is not. The stand-in is the real
``scripts/fake_llm.py`` app, so what comes back is what a deployment would get.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.db import _current_tenant_schema
from app.models import ChatConversation, EhrSystem
from app.models.ehr_route import GoalNavigationRequest, GoalNavigationResponse
from app.repositories import (
    InMemoryChatRepository,
    InMemoryEhrPromptRepository,
    InMemoryNotesRepository,
)
from app.services import (
    agent_llm_gateway,
    ehr_navigation_service,
    embedding_service,
    http_chat_llm_gateway,
    model_standin,
    structured_llm_gateway,
)
from app.services.ai_features import AIFeature
from app.services.chat_failover import FailoverChatLLMGateway, failover_chat_gateway
from app.services.chat_llm_gateway import FakeChatLLMGateway, StreamEvent
from app.services.chat_turn_service import ChatTurnService, TurnContext
from app.services.dictation_transcription import (
    AssemblyAiDictationTranscriber,
    StandinRoutedTranscriber,
    get_dictation_transcriber,
)
from app.services.hedged_structured_llm_gateway import (
    HedgedStructuredLLMGateway,
    generation_gateway,
)
from app.services.http_chat_llm_gateway import HttpChatLLMGateway
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.services.model_standin import model_standin_base_url
from app.services.structured_llm_gateway import (
    AnthropicStructuredLLMGateway,
    FakeStructuredLLMGateway,
    GeminiStructuredLLMGateway,
    StructuredCompletion,
    resolve_structured_llm_gateway,
)
from app.settings import Settings, get_settings
from fastapi.testclient import TestClient
from pydantic import SecretStr

from scripts.fake_llm import CHAT_TEXT, DICTATION_TEXT
from scripts.fake_llm import app as fake_llm_app

if TYPE_CHECKING:
    from collections.abc import Iterator

HOST = "http://fake-llm:8083"
#: The class the chat gateway uses, kept before a test replaces it.
_REAL_ASYNC_CLIENT = httpx.AsyncClient
BASE_URL = f"{HOST}/standin"
PRACTICE_A = "practice-a-id"
PRACTICE_B = "practice-b-id"
SCHEMA_A = "practice_a_schema"
SCHEMA_B = "practice_b_schema"
#: The platform's practices table, as the lookup reads it.
PRACTICES = {SCHEMA_A: PRACTICE_A, SCHEMA_B: PRACTICE_B}

NOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"subjective": {"type": "string"}},
}


def _make(**overrides: object) -> Settings:
    return Settings(database_url="postgresql://x:x@localhost:5432/x", **overrides)  # type: ignore[arg-type]


@contextmanager
def practice(schema: str | None) -> Iterator[None]:
    """Run the block as the request or job scoped to ``schema`` does."""
    token = _current_tenant_schema.set(schema)
    try:
        yield
    finally:
        _current_tenant_schema.reset(token)


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _PracticesSession:
    """Answers the lookup from ``PRACTICES`` and records each schema asked for."""

    def __init__(self, asked: list[str]) -> None:
        self._asked = asked

    def __enter__(self) -> _PracticesSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, statement: Any) -> _Result:
        compiled = statement.compile()
        assert " ".join(str(compiled).split()) == (
            "SELECT platform.practices.id FROM platform.practices "
            "WHERE platform.practices.schema_name = :schema_name_1"
        )
        schema = compiled.params["schema_name_1"]
        self._asked.append(schema)
        return _Result(PRACTICES.get(schema))


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """The platform lookup the helper makes; returns each schema it asked about."""
    asked: list[str] = []
    monkeypatch.setattr("app.db.create_standalone_session", lambda: _PracticesSession(asked))
    model_standin._forget_practices()
    yield asked
    model_standin._forget_practices()


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch, lookups: list[str]) -> Iterator[list[str]]:
    """Practice A listed; practice B not."""
    monkeypatch.setenv("MODEL_STANDIN_PRACTICES", f" {PRACTICE_A} ,, other-id ")
    monkeypatch.setenv("MODEL_STANDIN_BASE_URL", BASE_URL)
    get_settings.cache_clear()
    yield lookups
    get_settings.cache_clear()


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer every synchronous POST from the stand-in's app; return the URLs hit."""
    client = TestClient(fake_llm_app)
    urls: list[str] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        urls.append(url)
        kwargs.pop("timeout", None)
        kwargs.pop("headers", None)
        response: httpx.Response = client.post(url.removeprefix(HOST), **kwargs)
        return response

    monkeypatch.setattr(httpx, "post", post)
    return urls


class TestSettings:
    def test_both_empty_is_the_default_and_routes_no_one(self) -> None:
        settings = _make()

        assert settings.model_standin_practices == frozenset()
        assert settings.model_standin_base_url == ""

    def test_a_list_without_a_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="requires MODEL_STANDIN_BASE_URL"):
            _make(MODEL_STANDIN_PRACTICES=PRACTICE_A)

    def test_a_url_without_a_list_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names no practice"):
            _make(model_standin_base_url=BASE_URL)

    def test_a_list_of_only_separators_names_no_practice(self) -> None:
        with pytest.raises(ValueError, match="names no practice"):
            _make(MODEL_STANDIN_PRACTICES=" , ,", model_standin_base_url=BASE_URL)

    @pytest.mark.parametrize("environment", ["development", "staging", "production"])
    def test_both_load_in_any_environment(self, environment: str) -> None:
        settings = _make(
            environment=environment,
            MODEL_STANDIN_PRACTICES=f"{PRACTICE_A}, other-id",
            model_standin_base_url=BASE_URL,
        )

        assert settings.model_standin_practices == {PRACTICE_A, "other-id"}


class TestHelper:
    def test_no_list_routes_no_one_and_looks_nothing_up(self, lookups: list[str]) -> None:
        get_settings.cache_clear()
        with practice(SCHEMA_A):
            assert model_standin_base_url() is None
        assert lookups == []

    def test_a_listed_practice_gets_the_url(self, listed: list[str]) -> None:
        with practice(SCHEMA_A):
            assert model_standin_base_url() == BASE_URL

    def test_an_unlisted_practice_gets_none(self, listed: list[str]) -> None:
        with practice(SCHEMA_B):
            assert model_standin_base_url() is None

    @pytest.mark.parametrize("schema", [None, "practice", "platform"])
    def test_no_practice_in_scope_gets_none_without_a_lookup(
        self, listed: list[str], schema: str | None
    ) -> None:
        with practice(schema):
            assert model_standin_base_url() is None
        assert listed == []

    def test_a_schema_no_practice_owns_gets_none(self, listed: list[str]) -> None:
        with practice("practice_nobody"):
            assert model_standin_base_url() is None

    def test_each_schema_is_looked_up_once(self, listed: list[str]) -> None:
        for _ in range(3):
            with practice(SCHEMA_A):
                assert model_standin_base_url() == BASE_URL
            with practice(SCHEMA_B):
                assert model_standin_base_url() is None

        assert listed == [SCHEMA_A, SCHEMA_B]

    def test_the_rule_is_asked_per_call_not_remembered(self, listed: list[str]) -> None:
        with practice(SCHEMA_A):
            assert model_standin_base_url() == BASE_URL
        with practice(SCHEMA_B):
            assert model_standin_base_url() is None
        with practice(SCHEMA_A):
            assert model_standin_base_url() == BASE_URL

    def test_a_failed_lookup_means_the_real_model_and_is_asked_again_later(
        self, listed: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr(model_standin, "_monotonic", lambda: now[0])
        failures: list[str] = []

        def down() -> Any:
            failures.append("asked")
            raise ConnectionError("database unavailable")

        monkeypatch.setattr("app.db.create_standalone_session", down)
        with practice(SCHEMA_A):
            assert model_standin_base_url() is None
            now[0] += model_standin._LOOKUP_RETRY_SECONDS - 1
            assert model_standin_base_url() is None
        # Inside the window the database is not asked again.
        assert failures == ["asked"]

        monkeypatch.setattr("app.db.create_standalone_session", lambda: _PracticesSession(listed))
        now[0] += 1
        with practice(SCHEMA_A):
            assert model_standin_base_url() == BASE_URL
        assert listed == [SCHEMA_A]

    def test_a_failed_lookup_holds_back_only_its_own_schema(
        self, listed: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down() -> Any:
            raise ConnectionError("database unavailable")

        monkeypatch.setattr("app.db.create_standalone_session", down)
        with practice(SCHEMA_B):
            assert model_standin_base_url() is None

        monkeypatch.setattr("app.db.create_standalone_session", lambda: _PracticesSession(listed))
        with practice(SCHEMA_A):
            assert model_standin_base_url() == BASE_URL

    def test_the_async_form_gives_the_same_answers(self, listed: list[str]) -> None:
        async def ask(schema: str) -> str | None:
            with practice(schema):
                return await model_standin.amodel_standin_base_url()

        assert asyncio.run(ask(SCHEMA_A)) == BASE_URL
        assert asyncio.run(ask(SCHEMA_B)) is None

    def test_the_async_form_with_no_list_uses_no_thread(
        self, lookups: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        get_settings.cache_clear()

        async def no_thread(*_args: object) -> object:
            raise AssertionError("no thread wanted with no practice listed")

        monkeypatch.setattr(model_standin.asyncio, "to_thread", no_thread)
        with practice(SCHEMA_A):
            assert asyncio.run(model_standin.amodel_standin_base_url()) is None
        assert lookups == []


class TestStructured:
    def test_a_listed_practice_resolves_to_the_stand_in_whatever_the_model(
        self, listed: list[str]
    ) -> None:
        with practice(SCHEMA_A):
            for model in ("gemini-2.5-flash", "anthropic:claude-sonnet-4-5", "bedrock:x"):
                assert isinstance(resolve_structured_llm_gateway(model), HttpStructuredLLMGateway)

    def test_an_unlisted_practice_resolves_to_the_provider(self, listed: list[str]) -> None:
        with practice(SCHEMA_B):
            assert isinstance(
                resolve_structured_llm_gateway("gemini-2.5-flash"), GeminiStructuredLLMGateway
            )
            assert isinstance(
                resolve_structured_llm_gateway("anthropic:claude-sonnet-4-5"),
                AnthropicStructuredLLMGateway,
            )

    def test_a_listed_practice_drafts_from_the_stand_in(
        self, listed: list[str], stand_in: list[str]
    ) -> None:
        gateway = generation_gateway(AIFeature.NOTE_GENERATION)
        with practice(SCHEMA_A):
            completion = gateway.complete_structured(
                model="gemini-2.5-flash",
                system_prompt="",
                user_prompt="A transcript.",
                response_schema=NOTE_SCHEMA,
                max_output_tokens=100,
            )

        assert stand_in == [f"{BASE_URL}/v1/structured"]
        assert completion.data == {"subjective": "Stand-in draft for subjective."}

    def test_a_gateway_built_before_the_practice_was_in_scope_still_routes(
        self, listed: list[str], stand_in: list[str]
    ) -> None:
        """A queue worker builds its service in a dependency, then scopes itself."""
        with practice(None):
            gateway = generation_gateway(AIFeature.NOTE_GENERATION)
        with practice(SCHEMA_A):
            gateway.complete_structured(
                model="gemini-2.5-flash",
                system_prompt="",
                user_prompt="A transcript.",
                response_schema=NOTE_SCHEMA,
                max_output_tokens=100,
            )

        assert stand_in == [f"{BASE_URL}/v1/structured"]

    def test_hedged_legs_on_worker_threads_reach_the_stand_in(
        self, listed: list[str], stand_in: list[str]
    ) -> None:
        gateway = HedgedStructuredLLMGateway(fallbacks=("anthropic:claude-sonnet-4-5",))
        with practice(SCHEMA_A):
            result = gateway.complete_structured(
                model="gemini-2.5-flash",
                system_prompt="",
                user_prompt="9 to 5 Monday to Thursday.",
                response_schema={"type": "object", "properties": {"could_not_parse": {}}},
                max_output_tokens=100,
            )

        assert stand_in == [f"{BASE_URL}/v1/structured"]
        assert len(result.data["proposals"]) == 4

    def test_an_unlisted_practice_reaches_the_provider_and_not_the_stand_in(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data={"subjective": "From the model."})
        )
        monkeypatch.setattr(structured_llm_gateway, "_default_gateway_holder", [provider])
        gateway = generation_gateway(AIFeature.NOTE_GENERATION)
        with practice(SCHEMA_B):
            completion = gateway.complete_structured(
                model="gemini-2.5-flash",
                system_prompt="",
                user_prompt="A transcript.",
                response_schema=NOTE_SCHEMA,
                max_output_tokens=100,
            )

        assert completion.data == {"subjective": "From the model."}
        assert len(provider.calls) == 1
        assert stand_in == []


def _drain(gateway: Any) -> list[StreamEvent]:
    async def run() -> list[StreamEvent]:
        return [
            event
            async for event in gateway.stream_completion(
                model="gemini-2.5-flash",
                system_prompt="",
                prior_turns=[],
                new_user_text="Hello",
                max_output_tokens=100,
            )
        ]

    return asyncio.run(run())


@pytest.fixture
def chat_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Answer the chat gateway's POSTs from the stand-in's app; return the URLs hit."""
    urls: list[str] = []

    async def record(request: httpx.Request) -> None:
        urls.append(str(request.url))

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(
            transport=httpx.ASGITransport(app=fake_llm_app),
            event_hooks={"request": [record]},
            **kwargs,
        )

    monkeypatch.setattr(http_chat_llm_gateway.httpx, "AsyncClient", client)
    return urls


class TestChat:
    def test_a_stand_in_url_answers_from_the_stand_in(self, chat_stand_in: list[str]) -> None:
        provider = FakeChatLLMGateway(script=[StreamEvent(delta="From the model.")])
        gateway = failover_chat_gateway("briefing_chat", provider, standin_base_url=BASE_URL)
        events = _drain(gateway)

        assert isinstance(gateway, HttpChatLLMGateway)
        assert chat_stand_in == [f"{BASE_URL}/v1/chat"]
        assert events == [StreamEvent(delta=CHAT_TEXT), StreamEvent(finish_reason="stop")]
        assert provider.calls == []

    def test_no_stand_in_url_answers_from_the_provider(self, chat_stand_in: list[str]) -> None:
        provider = FakeChatLLMGateway(script=[StreamEvent(delta="From the model.")])
        gateway = failover_chat_gateway("briefing_chat", provider, standin_base_url=None)
        events = _drain(gateway)

        assert gateway is provider
        assert events == [StreamEvent(delta="From the model.")]
        assert chat_stand_in == []

    def test_no_stand_in_url_keeps_the_fallbacks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_FALLBACKS", '{"briefing_chat": "anthropic:claude-sonnet-4-5"}')
        get_settings.cache_clear()
        gateway = failover_chat_gateway("briefing_chat", FakeChatLLMGateway())

        assert isinstance(gateway, FailoverChatLLMGateway)

    def test_a_stand_in_that_fails_is_a_transient_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(**kwargs: Any) -> httpx.AsyncClient:
            def refuse(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(503)

            return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(refuse), **kwargs)

        monkeypatch.setattr(http_chat_llm_gateway.httpx, "AsyncClient", broken)
        [event] = _drain(HttpChatLLMGateway(BASE_URL))

        assert event.finish_reason == "error"
        assert event.error_code == "service_unavailable"
        assert event.transient is True

    def test_a_stand_in_that_refuses_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refusing(**kwargs: Any) -> httpx.AsyncClient:
            def refuse(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(422)

            return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(refuse), **kwargs)

        monkeypatch.setattr(http_chat_llm_gateway.httpx, "AsyncClient", refusing)
        [event] = _drain(HttpChatLLMGateway(BASE_URL))

        assert event.finish_reason == "error"
        assert event.transient is False

    def test_an_unreachable_stand_in_is_a_transient_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreachable(**kwargs: Any) -> httpx.AsyncClient:
            def refuse(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("refused", request=request)

            return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(refuse), **kwargs)

        monkeypatch.setattr(http_chat_llm_gateway.httpx, "AsyncClient", unreachable)
        [event] = _drain(HttpChatLLMGateway(BASE_URL))

        assert event.finish_reason == "error"
        assert event.transient is True


def _turn_service(gateway: FakeChatLLMGateway) -> ChatTurnService:
    """The real turn service, with in-memory repositories and one conversation."""
    chat_repo = InMemoryChatRepository()
    chat_repo.grant_all_access()
    chat_repo.add_conversation(
        ChatConversation(
            id="conv-standin",
            patient_id="patient-standin",
            owner_user_id="user-standin",
            title="Stand-in",
            caller_system_prompt="You are a clinical assistant.",
            caller_feature_key="chart_qa",
            created_at=datetime.now(UTC),
        ),
        "user-standin",
    )
    notes_repo = InMemoryNotesRepository()
    notes_repo.grant_all_access()
    ChatTurnService._conversation_locks.clear()
    return ChatTurnService(chat_repo=chat_repo, notes_repo=notes_repo, gateway=gateway)


_TURN = TurnContext(
    conversation_id="conv-standin",
    patient_id="patient-standin",
    requesting_user_id="user-standin",
    caller_system_prompt="You are a clinical assistant.",
    caller_feature_key="chart_qa",
    user_message="What have we tried?",
    source_selection=None,
    model="gemini-test-flash",
)


async def _turn_text(service: ChatTurnService) -> str:
    events = [e async for e in service.run_turn(_TURN)]
    return "".join(str(e.data["text"]) for e in events if e.kind == "delta")


class TestChatTurn:
    """The turn service asks the rule for each answer, off the event loop."""

    def test_a_listed_practice_answers_from_the_stand_in(
        self, listed: list[str], chat_stand_in: list[str]
    ) -> None:
        provider = FakeChatLLMGateway(
            script=[StreamEvent(delta="From the model."), StreamEvent(finish_reason="stop")]
        )
        with practice(SCHEMA_A):
            text = asyncio.run(_turn_text(_turn_service(provider)))

        assert text == CHAT_TEXT
        assert chat_stand_in == [f"{BASE_URL}/v1/chat"]
        assert provider.calls == []

    def test_an_unlisted_practice_answers_from_the_provider(
        self, listed: list[str], chat_stand_in: list[str]
    ) -> None:
        provider = FakeChatLLMGateway(
            script=[StreamEvent(delta="From the model."), StreamEvent(finish_reason="stop")]
        )
        with practice(SCHEMA_B):
            text = asyncio.run(_turn_text(_turn_service(provider)))

        assert text == "From the model."
        assert chat_stand_in == []
        assert len(provider.calls) == 1

    def test_a_slow_lookup_does_not_stall_the_event_loop(
        self, listed: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exhausted pool: each lookup waits, then fails. Other work keeps running."""
        stall = 0.6
        lookups_made: list[str] = []

        def exhausted() -> Any:
            lookups_made.append("asked")
            time.sleep(stall)
            raise TimeoutError("pool exhausted")

        monkeypatch.setattr("app.db.create_standalone_session", exhausted)

        async def turns_beside_a_ticker() -> tuple[float, list[str]]:
            gaps: list[float] = []
            done = asyncio.Event()

            async def ticker() -> None:
                last = time.monotonic()
                while not done.is_set():
                    await asyncio.sleep(0.02)
                    now = time.monotonic()
                    gaps.append(now - last)
                    last = now

            tick = asyncio.create_task(ticker())
            texts = []
            for _ in range(3):
                provider = FakeChatLLMGateway(
                    script=[StreamEvent(delta="From the model."), StreamEvent(finish_reason="stop")]
                )
                texts.append(await _turn_text(_turn_service(provider)))
            done.set()
            await tick
            return max(gaps), texts

        with practice(SCHEMA_B):
            worst_gap, texts = asyncio.run(turns_beside_a_ticker())

        # The lookup did wait (one stall), the turns fell back to the real
        # model, and the loop kept ticking throughout.
        assert lookups_made == ["asked"]
        assert texts == ["From the model."] * 3
        assert worst_gap < stall / 2


@pytest.fixture
def provider_clips(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    """The provider's transcribe, recording each clip instead of reaching it."""
    clips: list[bytes] = []

    def transcribe(_self: object, audio: bytes, _content_type: str) -> str:
        clips.append(audio)
        return "From the provider."

    monkeypatch.setattr(AssemblyAiDictationTranscriber, "transcribe", transcribe)
    return clips


def _with_provider() -> Settings:
    return get_settings().model_copy(
        update={
            "transcription_enabled": True,
            "transcription_provider": "assemblyai",
            "assemblyai_api_key": SecretStr("key"),
        }
    )


class TestDictation:
    def test_a_listed_practice_is_transcribed_by_the_stand_in(
        self, listed: list[str], stand_in: list[str], provider_clips: list[bytes]
    ) -> None:
        transcriber = get_dictation_transcriber(_with_provider())
        assert transcriber is not None
        with practice(SCHEMA_A):
            text = transcriber.transcribe(b"clip", "audio/webm")

        assert text == DICTATION_TEXT
        assert stand_in == [f"{BASE_URL}/v1/transcribe"]
        assert provider_clips == []

    def test_an_unlisted_practice_is_transcribed_by_the_provider(
        self, listed: list[str], stand_in: list[str], provider_clips: list[bytes]
    ) -> None:
        transcriber = get_dictation_transcriber(_with_provider())
        assert transcriber is not None
        with practice(SCHEMA_B):
            text = transcriber.transcribe(b"clip", "audio/webm")

        assert text == "From the provider."
        assert provider_clips == [b"clip"]
        assert stand_in == []

    def test_a_transcriber_built_before_the_practice_was_in_scope_still_routes(
        self, listed: list[str], stand_in: list[str], provider_clips: list[bytes]
    ) -> None:
        with practice(None):
            transcriber = get_dictation_transcriber(_with_provider())
        assert transcriber is not None
        with practice(SCHEMA_A):
            transcriber.transcribe(b"clip", "audio/webm")

        assert stand_in == [f"{BASE_URL}/v1/transcribe"]

    def test_with_no_list_the_provider_is_returned_unwrapped(self, lookups: list[str]) -> None:
        get_settings.cache_clear()
        transcriber = get_dictation_transcriber(_with_provider())

        assert isinstance(transcriber, AssemblyAiDictationTranscriber)

    def test_a_listed_deployment_wraps_the_provider(self, listed: list[str]) -> None:
        assert isinstance(get_dictation_transcriber(_with_provider()), StandinRoutedTranscriber)

    def test_with_no_provider_there_is_still_no_dictation(self, listed: list[str]) -> None:
        settings = get_settings().model_copy(update={"transcription_enabled": False})

        assert get_dictation_transcriber(settings) is None


class _SentinelError(Exception):
    """Raised where the real provider's client would have been built."""


def _no_provider(*_args: object, **_kwargs: object) -> object:
    raise _SentinelError


class TestEmbeddings:
    def test_a_listed_practice_is_embedded_by_the_stand_in(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(embedding_service, "vertex_genai_client", _no_provider)
        with practice(SCHEMA_A):
            vectors = embedding_service.GoogleEmbeddingService().embed_texts(["a", "b", "a"])

        assert stand_in == [f"{BASE_URL}/v1/embed"]
        assert [len(v) for v in vectors] == [768, 768, 768]
        assert vectors[0] == vectors[2]
        assert vectors[0] != vectors[1]

    def test_an_unlisted_practice_is_embedded_by_the_provider(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(embedding_service, "vertex_genai_client", _no_provider)
        with practice(SCHEMA_B), pytest.raises(RuntimeError) as raised:
            embedding_service.GoogleEmbeddingService().embed_texts(["a"])

        assert isinstance(raised.value.__cause__, _SentinelError)
        assert stand_in == []


def _tool_never_called(_arguments: dict[str, Any]) -> str:
    raise AssertionError("the stand-in calls no tools")


class TestAgent:
    def _run(self) -> agent_llm_gateway.AgentResult:
        return agent_llm_gateway.GeminiAgentLLMGateway().run_agent(
            model="gemini-2.5-flash",
            system_prompt="",
            user_prompt="Look into it.",
            tools=[agent_llm_gateway.ToolSpec("look", "", {"type": "object"}, _tool_never_called)],
            response_schema=NOTE_SCHEMA,
        )

    def test_a_listed_practice_is_answered_by_the_stand_in(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_llm_gateway, "vertex_genai_client", _no_provider)
        with practice(SCHEMA_A):
            result = self._run()

        assert stand_in == [f"{BASE_URL}/v1/structured"]
        assert result.data == {"subjective": "Stand-in draft for subjective."}
        assert result.tool_calls == []

    def test_an_unlisted_practice_reaches_the_provider(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_llm_gateway, "vertex_genai_client", _no_provider)
        with practice(SCHEMA_B), pytest.raises(_SentinelError):
            self._run()

        assert stand_in == []


class TestEhrNavigation:
    @staticmethod
    def _navigate() -> GoalNavigationResponse:
        service = ehr_navigation_service.GeminiEhrNavigationService(
            model="gemini-2.5-flash-lite", prompt_repo=InMemoryEhrPromptRepository()
        )

        async def prompt(_ehr_system: str) -> str:
            return "Navigate."

        service.get_ehr_prompt = prompt  # type: ignore[method-assign,assignment]
        request = GoalNavigationRequest(
            ehr_system=next(iter(EhrSystem)),
            goal="Open the note",
            current_url="https://ehr.example/",
            dom_snapshot="<a>Notes</a>",
        )
        return asyncio.run(service.navigate(request))

    def test_a_listed_practice_is_answered_by_the_stand_in(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ehr_navigation_service, "vertex_genai_client", _no_provider)
        monkeypatch.setattr(ehr_navigation_service, "generate_text", _no_provider)
        with practice(SCHEMA_A):
            response = self._navigate()

        assert stand_in == [f"{BASE_URL}/v1/structured"]
        # The stand-in's reply is checked as a model's: no action it names is taken.
        assert response.action == "none"
        assert response.is_on_target_page is False

    def test_an_unlisted_practice_reaches_the_provider(
        self, listed: list[str], stand_in: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[str] = []

        def generate_text(**kwargs: Any) -> str:
            sent.append(kwargs["model"])
            return (
                '{"action": "click", "selector": "a", "reasoning": "", '
                '"confidence": 0.9, "is_on_target_page": false}'
            )

        monkeypatch.setattr(ehr_navigation_service, "vertex_genai_client", object)
        monkeypatch.setattr(ehr_navigation_service, "generate_text", generate_text)
        with practice(SCHEMA_B):
            response = self._navigate()

        assert sent == ["gemini-2.5-flash-lite"]
        assert response.action == "click"
        assert stand_in == []
