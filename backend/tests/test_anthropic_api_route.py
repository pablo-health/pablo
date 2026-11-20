# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``anthropic-api:`` route: Anthropic's API called directly with the deployment's key.

The API is faked at the HTTP transport, so the real SDK builds and sends the
request and these tests read what would have gone over the wire.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from backend.app.services import structured_llm_gateway as structured_module
from backend.app.services.anthropic_api_client import (
    AnthropicApiRouteNotConfiguredError,
    anthropic_api_client,
    anthropic_api_route_configured,
)
from backend.app.services.llm_provider import LLMProvider, strip_provider_prefix
from backend.app.services.structured_llm_gateway import (
    AnthropicApiStructuredLLMGateway,
    AnthropicStructuredLLMGateway,
    resolve_structured_llm_gateway,
)
from backend.app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

KEY = "sk-route-test-0123456789abcdef"
MODEL = "anthropic-api:claude-haiku-5-5"
SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string"}},
    "required": ["kind"],
}


@pytest.fixture
def route_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Set (or clear) the route's key; the settings and gateway caches are reset around it."""

    def set_key(value: str) -> None:
        monkeypatch.setenv("ANTHROPIC_API_ROUTE_KEY", value)
        get_settings.cache_clear()
        structured_module._anthropic_api_gateways.clear()

    monkeypatch.delenv("ANTHROPIC_API_ROUTE_KEY", raising=False)
    get_settings.cache_clear()
    structured_module._anthropic_api_gateways.clear()
    yield set_key
    get_settings.cache_clear()
    structured_module._anthropic_api_gateways.clear()


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


def _gateway(api: _Api) -> AnthropicApiStructuredLLMGateway:
    gateway = resolve_structured_llm_gateway(MODEL)
    assert isinstance(gateway, AnthropicApiStructuredLLMGateway)
    gateway._client = anthropic_api_client(http_client=api.client())
    return gateway


def _complete(gateway: AnthropicStructuredLLMGateway) -> Any:
    return gateway.complete_structured(
        model=MODEL,
        system_prompt="Sort the file.",
        user_prompt="file text",
        response_schema=SCHEMA,
        max_output_tokens=64,
    )


def test_the_prefix_is_a_known_provider_and_is_stripped() -> None:
    assert LLMProvider.ANTHROPIC_API == "anthropic-api"
    assert strip_provider_prefix(MODEL) == "claude-haiku-5-5"


def test_the_prefix_resolves_to_the_direct_gateway_and_anthropic_still_to_vertex(
    route_key: Callable[[str], None],
) -> None:
    route_key(KEY)
    direct = resolve_structured_llm_gateway(MODEL)
    vertex = resolve_structured_llm_gateway("anthropic:claude-haiku-5-5")
    assert isinstance(direct, AnthropicApiStructuredLLMGateway)
    assert type(vertex) is AnthropicStructuredLLMGateway


def test_a_call_goes_to_the_api_with_the_configured_key(route_key: Callable[[str], None]) -> None:
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


def test_the_usage_record_names_anthropic_as_the_provider(
    route_key: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    route_key(KEY)
    seen: list[str] = []
    real_span = structured_module.llm_span

    def spy(request: Any) -> Any:
        seen.append(request.provider)
        return real_span(request)

    monkeypatch.setattr(structured_module, "llm_span", spy)
    _complete(_gateway(_Api()))
    assert seen == ["anthropic"]


def test_no_key_means_not_configured_and_no_request(route_key: Callable[[str], None]) -> None:
    route_key("  ")
    assert anthropic_api_route_configured() is False
    gateway = resolve_structured_llm_gateway(MODEL)
    with pytest.raises(AnthropicApiRouteNotConfiguredError):
        _complete(gateway)


def test_a_key_means_configured(route_key: Callable[[str], None]) -> None:
    route_key(KEY)
    assert anthropic_api_route_configured() is True


@pytest.mark.parametrize(
    ("status", "error_type", "message"),
    [
        (401, "authentication_error", "invalid x-api-key"),
        (400, "invalid_request_error", "Your credit balance is too low."),
    ],
)
def test_a_failed_call_never_carries_the_key(
    route_key: Callable[[str], None],
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


def test_the_missing_key_error_does_not_echo_a_value() -> None:
    assert "sk-" not in str(AnthropicApiRouteNotConfiguredError())
