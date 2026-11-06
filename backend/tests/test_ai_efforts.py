# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A feature's reasoning effort: the setting, and the Claude-on-Vertex gateways that send it.

The Claude 5 family reasons before it answers, at the highest effort unless told
otherwise. ``AI_EFFORTS`` names an effort per feature; the gateways that serve a
feature send it as ``output_config.effort`` to a model that takes one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from app.services.anthropic_vertex_chat_llm_gateway import (
    AnthropicVertexChatLLMGateway,
    get_anthropic_vertex_chat_llm_gateway,
)
from app.services.chat_failover import FailoverChatLLMGateway
from app.services.chat_llm_gateway import FakeChatLLMGateway
from app.services.hedged_structured_llm_gateway import (
    HedgedStructuredLLMGateway,
    generation_gateway,
)
from app.services.structured_llm_gateway import (
    AnthropicStructuredLLMGateway,
    resolve_structured_llm_gateway,
)
from app.settings import Settings, get_settings
from pydantic import ValidationError

from .test_anthropic_vertex_chat_llm_gateway import _FakeClient, _FakeStream

if TYPE_CHECKING:
    from collections.abc import Iterator

_DB = "postgresql://x:x@localhost:5432/x"
FEATURE = "briefing_chat"


def _settings() -> Settings:
    return Settings(database_url=_DB, environment="development")


@pytest.fixture(autouse=True)
def _no_db_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.db.assert_no_held_db_connection", lambda _label: None)


@pytest.fixture
def efforts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """``briefing_chat`` asks for ``low``; ``note_generation`` for ``high``."""
    monkeypatch.setenv("AI_EFFORTS", '{"briefing_chat": "low", "note_generation": "high"}')
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSetting:
    def test_nothing_is_configured_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AI_EFFORTS", raising=False)
        configured = _settings()

        assert configured.ai_efforts == {}
        assert configured.effort_for(FEATURE) is None

    def test_it_is_read_from_the_environment_as_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_EFFORTS", '{"briefing_chat": "low", "note_generation": "high"}')
        configured = _settings()

        assert configured.effort_for("briefing_chat") == "low"
        assert configured.effort_for("note_generation") == "high"

    def test_a_feature_not_named_has_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_EFFORTS", '{"briefing_chat": "low"}')
        configured = _settings()

        assert configured.effort_for("note_import") is None
        assert configured.effort_for(None) is None

    @pytest.mark.parametrize("value", ["low", "medium", "high", "xhigh", "max"])
    def test_every_effort_the_api_lists_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("AI_EFFORTS", f'{{"briefing_chat": "{value}"}}')

        assert _settings().effort_for(FEATURE) == value

    @pytest.mark.parametrize("value", ["fast", "LOW", "", "none"])
    def test_a_value_outside_the_list_fails_at_startup(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """A typo must stop the process, not silently run at the default effort."""
        monkeypatch.setenv("AI_EFFORTS", f'{{"briefing_chat": "{value}"}}')

        with pytest.raises(ValidationError):
            _settings()


def _stream_request(model: str, *, effort: str | None) -> dict[str, Any]:
    """The request the chat gateway builds for ``model`` at ``effort``."""
    client = _FakeClient(_FakeStream(["Hi"]))
    gateway = AnthropicVertexChatLLMGateway(client=client, effort=effort)

    async def collect() -> None:
        async for _ in gateway.stream_completion(
            model=model,
            system_prompt="Be brief.",
            prior_turns=[],
            new_user_text="Hello",
            max_output_tokens=100,
        ):
            pass

    asyncio.run(collect())
    return client.requests[0]


class TestChatGateway:
    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_the_effort_is_sent_to_a_5_family_model(self, effort: str) -> None:
        request = _stream_request("anthropic:claude-haiku-5-5", effort=effort)

        assert request["output_config"] == {"effort": effort}

    def test_no_effort_is_sent_when_none_is_set(self) -> None:
        assert "output_config" not in _stream_request("anthropic:claude-haiku-5-5", effort=None)

    def test_an_older_model_is_never_sent_one(self) -> None:
        request = _stream_request("anthropic:claude-sonnet-4-6", effort="low")

        assert "output_config" not in request

    def test_one_gateway_serves_each_effort(self) -> None:
        low = get_anthropic_vertex_chat_llm_gateway("low")

        assert get_anthropic_vertex_chat_llm_gateway("low") is low
        assert get_anthropic_vertex_chat_llm_gateway(None) is not low
        assert get_anthropic_vertex_chat_llm_gateway() is get_anthropic_vertex_chat_llm_gateway(
            None
        )


class TestFailoverPicksTheFeaturesEffort:
    """The wrapper knows its feature, so it picks the gateway for that feature's effort."""

    @staticmethod
    def _resolved(feature: str | None) -> AnthropicVertexChatLLMGateway:
        own = FakeChatLLMGateway(scripts=[])
        resolved = FailoverChatLLMGateway(own, [], feature=feature or "")._default_resolve(
            "anthropic:claude-haiku-5-5"
        )
        assert isinstance(resolved, AnthropicVertexChatLLMGateway)
        return resolved

    @pytest.mark.usefixtures("efforts")
    def test_a_feature_with_an_effort_gets_that_efforts_gateway(self) -> None:
        assert self._resolved(FEATURE) is get_anthropic_vertex_chat_llm_gateway("low")
        assert self._resolved("note_generation") is get_anthropic_vertex_chat_llm_gateway("high")

    @pytest.mark.usefixtures("efforts")
    def test_a_feature_without_one_gets_the_default_gateway(self) -> None:
        assert self._resolved("note_import") is get_anthropic_vertex_chat_llm_gateway(None)

    @pytest.mark.usefixtures("efforts")
    def test_a_wrapper_with_no_feature_gets_the_default_gateway(self) -> None:
        assert self._resolved(None) is get_anthropic_vertex_chat_llm_gateway(None)


class _Messages:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.sent = kwargs
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="tool_use", name="emit_structured_output", input={"a": "b"})
            ],
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


_SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def _structured_request(model: str, *, effort: str | None) -> dict[str, Any]:
    messages = _Messages()
    AnthropicStructuredLLMGateway(
        client=SimpleNamespace(messages=messages), effort=effort
    ).complete_structured(
        model=model,
        system_prompt="s",
        user_prompt="u",
        response_schema=_SCHEMA,
        max_output_tokens=60,
    )
    return messages.sent


class TestStructuredGateway:
    @pytest.mark.parametrize("effort", ["low", "medium", "high"])
    def test_the_effort_is_sent_to_a_5_family_model(self, effort: str) -> None:
        sent = _structured_request("anthropic:claude-haiku-5-5", effort=effort)

        assert sent["output_config"] == {"effort": effort}

    def test_no_effort_is_sent_when_none_is_set(self) -> None:
        assert "output_config" not in _structured_request("anthropic:claude-haiku-5-5", effort=None)

    def test_an_older_model_is_never_sent_one(self) -> None:
        assert "output_config" not in _structured_request(
            "anthropic:claude-sonnet-4-6", effort="low"
        )


class TestStructuredResolution:
    @staticmethod
    def _effort_of(gateway: object) -> str | None:
        assert isinstance(gateway, AnthropicStructuredLLMGateway)
        return gateway._effort

    @pytest.mark.usefixtures("efforts")
    def test_a_feature_with_an_effort_gets_a_gateway_that_asks_for_it(self) -> None:
        gateway = resolve_structured_llm_gateway("anthropic:claude-haiku-5-5", feature=FEATURE)

        assert self._effort_of(gateway) == "low"

    @pytest.mark.usefixtures("efforts")
    def test_a_call_with_no_feature_asks_for_none(self) -> None:
        assert self._effort_of(resolve_structured_llm_gateway("anthropic:claude-haiku-5-5")) is None

    @pytest.mark.usefixtures("efforts")
    def test_a_feature_without_an_entry_asks_for_none(self) -> None:
        gateway = resolve_structured_llm_gateway(
            "anthropic:claude-haiku-5-5", feature="note_import"
        )

        assert self._effort_of(gateway) is None

    @pytest.mark.usefixtures("efforts")
    def test_the_same_effort_is_the_same_gateway(self) -> None:
        a = resolve_structured_llm_gateway("anthropic:claude-haiku-5-5", feature=FEATURE)
        b = resolve_structured_llm_gateway("anthropic:claude-sonnet-5-5", feature=FEATURE)

        assert a is b

    @pytest.mark.usefixtures("efforts")
    def test_another_provider_is_not_affected(self) -> None:
        gateway = resolve_structured_llm_gateway("gemini-3.5-flash", feature=FEATURE)

        assert not isinstance(gateway, AnthropicStructuredLLMGateway)


class TestHedgedGatewayCarriesItsFeature:
    """A hedged gateway resolves each leg for the feature it serves."""

    @pytest.mark.usefixtures("efforts")
    def test_its_own_resolver_reads_the_features_effort(self) -> None:
        gateway = HedgedStructuredLLMGateway(feature=FEATURE)

        leg = gateway._resolve("anthropic:claude-haiku-5-5")

        assert isinstance(leg, AnthropicStructuredLLMGateway)
        assert leg._effort == "low"

    @pytest.mark.usefixtures("efforts")
    def test_a_resolver_passed_in_is_used_as_given(self) -> None:
        stand_in = FakeChatLLMGateway(scripts=[])
        gateway = HedgedStructuredLLMGateway(feature=FEATURE, resolve=lambda _model: stand_in)  # type: ignore[arg-type,return-value]

        assert gateway._resolve("anthropic:claude-haiku-5-5") is stand_in

    def test_the_long_call_gateway_for_a_feature_with_fallbacks_reads_its_effort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_EFFORTS", '{"briefing_chat": "low"}')
        monkeypatch.setenv("AI_FALLBACKS", '{"briefing_chat": "google:gemini-3.5-flash"}')
        get_settings.cache_clear()
        try:
            gateway = generation_gateway(FEATURE)
            assert isinstance(gateway, HedgedStructuredLLMGateway)
            leg = gateway._resolve("anthropic:claude-haiku-5-5")
            assert isinstance(leg, AnthropicStructuredLLMGateway)
            assert leg._effort == "low"
        finally:
            get_settings.cache_clear()
