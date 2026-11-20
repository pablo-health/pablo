# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``anthropic-direct:`` route: Anthropic's API called directly with the deployment's key.

Only from a feature the deployment allows (``anthropic_direct_features``).

The API is faked at the HTTP transport, so the real SDK builds and sends the
request and these tests read what would have gone over the wire.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from backend.app.reliability import LLM_REQUEST, RetryPolicy
from backend.app.services import structured_llm_gateway as structured_module
from backend.app.services.anthropic_direct_client import (
    AnthropicDirectNotConfiguredError,
    anthropic_direct_client,
    anthropic_direct_configured,
)
from backend.app.services.llm_provider import LLMProvider, strip_provider_prefix
from backend.app.services.llm_telemetry import llm_feature
from backend.app.services.structured_llm_gateway import (
    AnthropicDirectStructuredLLMGateway,
    AnthropicStructuredLLMGateway,
    resolve_structured_llm_gateway,
)
from backend.app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

KEY = "sk-dummy-route-key-0123456789abcdef"
MODEL = "anthropic-direct:claude-haiku-5-5"
FEATURE = "file_sorting"
SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string"}},
    "required": ["kind"],
}


@pytest.fixture
def route_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Set the route's key and allowed features; caches are reset around it."""

    def set_key(value: str, features: str = FEATURE) -> None:
        monkeypatch.setenv("ANTHROPIC_DIRECT_API_KEY", value)
        monkeypatch.setenv("ANTHROPIC_DIRECT_FEATURES", features)
        get_settings.cache_clear()
        structured_module._anthropic_direct_gateways.clear()

    monkeypatch.delenv("ANTHROPIC_DIRECT_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_DIRECT_FEATURES", raising=False)
    get_settings.cache_clear()
    structured_module._anthropic_direct_gateways.clear()
    yield set_key
    get_settings.cache_clear()
    structured_module._anthropic_direct_gateways.clear()


def _tool_use_body() -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "emit_structured_output",
                "input": {"kind": "receipt"},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 40, "output_tokens": 7},
    }


class _Api:
    """A fake Anthropic API: records each request and answers with ``status`` and ``body``."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else _tool_use_body()
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))


def _gateway(api: _Api) -> AnthropicDirectStructuredLLMGateway:
    gateway = resolve_structured_llm_gateway(MODEL)
    assert isinstance(gateway, AnthropicDirectStructuredLLMGateway)
    gateway._client = anthropic_direct_client(http_client=api.client())
    return gateway


def _complete(
    gateway: AnthropicStructuredLLMGateway,
    feature: str | None = FEATURE,
    *,
    model: str = MODEL,
    retry_policy: RetryPolicy | None = None,
) -> Any:
    with llm_feature(feature):
        return gateway.complete_structured(
            model=model,
            system_prompt="Sort the file.",
            user_prompt="file text",
            response_schema=SCHEMA,
            max_output_tokens=64,
            retry_policy=retry_policy,
        )


def _providers_seen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the ``provider`` of every structured span opened from now on."""
    seen: list[str] = []
    real_span = structured_module.llm_span

    def spy(request: Any) -> Any:
        seen.append(request.provider)
        return real_span(request)

    monkeypatch.setattr(structured_module, "llm_span", spy)
    return seen


def test_the_prefix_is_a_known_provider_and_is_stripped() -> None:
    assert LLMProvider.ANTHROPIC_DIRECT == "anthropic-direct"
    assert strip_provider_prefix(MODEL) == "claude-haiku-5-5"


def test_the_prefix_resolves_to_the_direct_gateway_and_anthropic_still_to_vertex(
    route_key: Callable[..., None],
) -> None:
    route_key(KEY)
    direct = resolve_structured_llm_gateway(MODEL)
    vertex = resolve_structured_llm_gateway("anthropic:claude-haiku-5-5")
    assert isinstance(direct, AnthropicDirectStructuredLLMGateway)
    assert type(vertex) is AnthropicStructuredLLMGateway


def test_a_call_goes_to_the_api_with_the_configured_key(route_key: Callable[..., None]) -> None:
    route_key(KEY)
    api = _Api()
    result = _complete(_gateway(api))

    assert result.data == {"kind": "receipt"}
    assert result.output_tokens == 7
    (sent,) = api.requests
    assert sent.url.host == "api.anthropic.com"
    assert sent.url.path == "/v1/messages"
    assert sent.headers["x-api-key"] == KEY
    body = json.loads(sent.content)
    assert body["model"] == "claude-haiku-5-5"
    assert body["tools"][0]["input_schema"] == SCHEMA
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_the_usage_record_names_the_direct_route_as_the_provider(
    route_key: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Not ``anthropic``, which Claude-on-Vertex chat calls carry: a report
    # splitting spend by provider must tell Anthropic's invoice from Google's.
    route_key(KEY)
    seen = _providers_seen(monkeypatch)
    _complete(_gateway(_Api()))
    assert seen == ["anthropic-direct"]


def test_claude_on_vertex_structured_calls_are_still_labelled_google(
    route_key: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Vertex gateway's label is the record every existing Claude
    # structured usage row carries; the direct route must not move it.
    route_key(KEY)
    seen = _providers_seen(monkeypatch)
    vertex = AnthropicStructuredLLMGateway(
        client=anthropic_direct_client(http_client=_Api().client())
    )
    _complete(vertex, model="anthropic:claude-haiku-5-5")
    assert seen == ["google"]


def test_the_feature_effort_reaches_the_direct_route(
    route_key: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_EFFORTS", json.dumps({FEATURE: "low"}))
    route_key(KEY)
    api = _Api()
    gateway = resolve_structured_llm_gateway(MODEL, feature=FEATURE)
    assert isinstance(gateway, AnthropicDirectStructuredLLMGateway)
    gateway._client = anthropic_direct_client(http_client=api.client())
    _complete(gateway)
    (sent,) = api.requests
    assert json.loads(sent.content)["output_config"] == {"effort": "low"}


def test_a_rate_limit_is_retried_by_the_policy_alone(route_key: Callable[..., None]) -> None:
    # The SDK must not retry on its own: its retries would multiply with
    # ``call_with_retry``'s, all on the deployment's own rate limit.
    route_key(KEY)
    api = _Api(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
    policy = RetryPolicy(max_attempts=LLM_REQUEST.max_attempts, base_delay=0.0, max_delay=0.0)
    with pytest.raises(RuntimeError):
        _complete(_gateway(api), retry_policy=policy)
    assert len(api.requests) == LLM_REQUEST.max_attempts


def test_no_key_means_not_configured_and_no_request(route_key: Callable[..., None]) -> None:
    route_key("  ")
    assert anthropic_direct_configured(FEATURE) is False
    gateway = resolve_structured_llm_gateway(MODEL)
    with pytest.raises(AnthropicDirectNotConfiguredError):
        _complete(gateway)
    assert gateway._client is None


def test_a_key_and_an_allowed_feature_mean_configured(route_key: Callable[..., None]) -> None:
    route_key(KEY)
    assert anthropic_direct_configured(FEATURE) is True
    assert anthropic_direct_configured("note_generation") is False
    assert anthropic_direct_configured(None) is False


@pytest.mark.parametrize("feature", ["note_generation", None])
def test_a_feature_not_allowed_is_refused_before_any_request(
    route_key: Callable[..., None], feature: str | None
) -> None:
    route_key(KEY)
    api = _Api()
    gateway = _gateway(api)
    with pytest.raises(AnthropicDirectNotConfiguredError, match="not allowed"):
        _complete(gateway, feature)
    assert api.requests == []


def test_no_features_listed_allows_none(route_key: Callable[..., None]) -> None:
    route_key(KEY, features="")
    api = _Api()
    with pytest.raises(AnthropicDirectNotConfiguredError):
        _complete(_gateway(api))
    assert api.requests == []


def test_a_star_allows_any_feature(route_key: Callable[..., None]) -> None:
    route_key(KEY, features=" * ")
    api = _Api()
    _complete(_gateway(api), "note_generation")
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    ("status", "error_type", "message"),
    [
        (401, "authentication_error", "invalid x-api-key"),
        (400, "invalid_request_error", "Your credit balance is too low."),
    ],
)
def test_a_failed_call_never_carries_the_key(
    route_key: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
    status: int,
    error_type: str,
    message: str,
) -> None:
    route_key(KEY)
    api = _Api(status, {"type": "error", "error": {"type": error_type, "message": message}})
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError) as raised:
        _complete(_gateway(api))

    assert len(api.requests) == 1
    chain: list[BaseException] = []
    exc: BaseException | None = raised.value
    while exc is not None:
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    assert all(KEY not in str(e) and KEY not in repr(e) for e in chain)
    assert KEY not in caplog.text
    assert all(KEY not in record.getMessage() for record in caplog.records)


def test_the_missing_key_error_does_not_echo_a_value(route_key: Callable[..., None]) -> None:
    route_key("")
    with pytest.raises(AnthropicDirectNotConfiguredError) as raised:
        anthropic_direct_client()
    assert "sk-" not in str(raised.value)
