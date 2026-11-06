# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claude on Vertex streams a chat answer the way the other chat gateways do.

The SDK client is replaced by a fake that replays a stream, so these pin what
the gateway sends and how it reads what comes back. A run against the real
endpoint is the PR's evidence that the fake matches the SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from anthropic import AsyncAnthropicVertex
from app.services import anthropic_vertex_chat_llm_gateway as module
from app.services.anthropic_vertex_chat_llm_gateway import (
    AnthropicVertexChatLLMGateway,
    get_anthropic_vertex_chat_llm_gateway,
    system_blocks,
    to_anthropic_messages,
)
from app.services.chat_llm_gateway import (
    OPENING_USER_TURN,
    PATIENT_CONTEXT_DELIMITER,
    StreamEvent,
    UserAssistantTurn,
)

_REQUEST = httpx.Request("POST", "https://aiplatform.googleapis.test/v1/messages")
SECRET = "Jane Roe asked about the lighthouse"


@pytest.fixture(autouse=True)
def _no_db_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """No pooled DB connection is held in these tests; the guard has nothing to check."""
    monkeypatch.setattr("app.db.assert_no_held_db_connection", lambda _label: None)


def _usage(
    *, input_tokens: int = 100, output_tokens: int = 7, cache_read: int | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
    )


class _FakeStream:
    """What ``client.messages.stream(...)`` returns: an async context manager."""

    def __init__(
        self,
        chunks: list[str],
        *,
        stop_reason: str | None = "end_turn",
        usage: SimpleNamespace | None = None,
        fail_with: BaseException | None = None,
        fail_before: int = 0,
    ) -> None:
        self._chunks = chunks
        self._stop_reason = stop_reason
        self._usage = usage or _usage()
        self._fail_with = fail_with
        self._fail_before = fail_before
        self.closed = False

    async def __aenter__(self) -> _FakeStream:
        if self._fail_with is not None and self._fail_before == 0:
            raise self._fail_with
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    @property
    def text_stream(self) -> Any:
        async def replay() -> Any:
            for index, chunk in enumerate(self._chunks):
                if self._fail_with is not None and index == self._fail_before:
                    raise self._fail_with
                yield chunk

        return replay()

    async def get_final_message(self) -> SimpleNamespace:
        return SimpleNamespace(stop_reason=self._stop_reason, usage=self._usage)


class _FakeClient:
    def __init__(self, stream: _FakeStream) -> None:
        self.requests: list[dict[str, Any]] = []
        self._stream = stream
        self.messages = SimpleNamespace(stream=self._open)

    def _open(self, **request: Any) -> _FakeStream:
        self.requests.append(request)
        return self._stream


def _run(
    stream: _FakeStream,
    *,
    model: str = "anthropic:claude-sonnet-4-6",
    system_prompt: str = "Be brief.",
    prior_turns: list[UserAssistantTurn] | None = None,
    new_user_text: str = "Hello",
    temperature: float = 0.4,
) -> tuple[list[StreamEvent], _FakeClient]:
    client = _FakeClient(stream)
    gateway = AnthropicVertexChatLLMGateway(client=client)

    async def collect() -> list[StreamEvent]:
        return [
            event
            async for event in gateway.stream_completion(
                model=model,
                system_prompt=system_prompt,
                prior_turns=prior_turns or [],
                new_user_text=new_user_text,
                max_output_tokens=500,
                temperature=temperature,
            )
        ]

    return asyncio.run(collect()), client


def _status_error(status: int) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        "service said no", response=httpx.Response(status, request=_REQUEST), body=None
    )


class TestAnswering:
    def test_words_stream_in_order_and_the_answer_ends_with_its_token_count(self) -> None:
        events, _ = _run(_FakeStream(["Hel", "lo ", "there"], usage=_usage(output_tokens=9)))

        assert [e.delta for e in events[:-1]] == ["Hel", "lo ", "there"]
        assert events[-1] == StreamEvent(finish_reason="stop", output_tokens=9)

    def test_an_empty_chunk_is_not_a_word(self) -> None:
        events, _ = _run(_FakeStream(["", "Hi", ""]))

        assert [e.delta for e in events[:-1]] == ["Hi"]

    def test_the_provider_prefix_is_not_sent_to_the_endpoint(self) -> None:
        _, client = _run(_FakeStream(["Hi"]), model="anthropic:claude-sonnet-4-6")

        assert client.requests[0]["model"] == "claude-sonnet-4-6"
        assert client.requests[0]["max_tokens"] == 500

    def test_the_stream_is_closed_when_the_answer_ends(self) -> None:
        stream = _FakeStream(["Hi"])

        _run(stream)

        assert stream.closed


class TestRequestShape:
    def test_a_model_that_accepts_temperature_gets_it(self) -> None:
        _, client = _run(_FakeStream(["Hi"]), model="anthropic:claude-sonnet-4-6", temperature=0.4)

        assert client.requests[0]["temperature"] == 0.4

    @pytest.mark.parametrize(
        "model",
        [
            "anthropic:claude-haiku-5-5",
            "anthropic:claude-sonnet-5-5",
            "anthropic:claude-opus-5-5",
        ],
    )
    def test_a_5_5_model_is_not_sent_temperature(self, model: str) -> None:
        """Vertex answers 400 "temperature is deprecated for this model" for these."""
        _, client = _run(_FakeStream(["Hi"]), model=model, temperature=0.4)

        assert "temperature" not in client.requests[0]

    def test_only_the_stable_prompt_is_cached_and_the_chart_follows_it(self) -> None:
        prompt = f"Be brief.{PATIENT_CONTEXT_DELIMITER}{SECRET}"

        _, client = _run(_FakeStream(["Hi"]), system_prompt=prompt)

        stable, chart = client.requests[0]["system"]
        assert stable == {
            "type": "text",
            "text": "Be brief.",
            "cache_control": {"type": "ephemeral"},
        }
        assert chart == {"type": "text", "text": f"{PATIENT_CONTEXT_DELIMITER}{SECRET}"}
        assert "cache_control" not in chart

    def test_a_prompt_with_no_chart_is_one_cached_block(self) -> None:
        assert system_blocks("Be brief.") == [
            {"type": "text", "text": "Be brief.", "cache_control": {"type": "ephemeral"}}
        ]

    def test_a_prompt_that_is_only_a_chart_caches_nothing(self) -> None:
        """An empty cached block would be rejected; the chart is never cached anyway."""
        blocks = system_blocks(f"{PATIENT_CONTEXT_DELIMITER}{SECRET}")

        assert len(blocks) == 1
        assert "cache_control" not in blocks[0]

    def test_an_empty_prompt_sends_no_system_field(self) -> None:
        _, client = _run(_FakeStream(["Hi"]), system_prompt="")

        assert "system" not in client.requests[0]

    def test_turns_open_with_the_user_and_alternate(self) -> None:
        turns = [
            UserAssistantTurn("assistant", "Earlier answer"),
            UserAssistantTurn("user", "First"),
            UserAssistantTurn("user", "Second"),
            UserAssistantTurn("assistant", ""),
        ]

        # The empty assistant turn is skipped, so "Now" follows "Second" from the
        # same side and joins it: two user turns in a row would be rejected.
        assert to_anthropic_messages(turns, "Now") == [
            {"role": "user", "content": OPENING_USER_TURN},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": "First\n\nSecond\n\nNow"},
        ]


class TestFinishing:
    @pytest.mark.parametrize(
        ("stop_reason", "expected"),
        [
            ("end_turn", "stop"),
            ("stop_sequence", "stop"),
            ("max_tokens", "length"),
            ("refusal", "safety"),
            ("pause_turn", "stop"),
            (None, "stop"),
        ],
    )
    def test_the_stop_reason_decides_the_finish(
        self, stop_reason: str | None, expected: str
    ) -> None:
        events, _ = _run(_FakeStream(["An answer"], stop_reason=stop_reason))

        assert events[-1].finish_reason == expected

    def test_an_answer_that_stops_without_a_word_is_a_refusal(self) -> None:
        events, _ = _run(_FakeStream([], stop_reason="end_turn"))

        assert events == [StreamEvent(finish_reason="safety", output_tokens=7)]

    def test_a_decline_written_as_text_is_an_answer(self) -> None:
        """Claude usually declines in plain words with end_turn; nothing reads those words."""
        events, _ = _run(_FakeStream(["I can't help with that."], stop_reason="end_turn"))

        assert events[-1].finish_reason == "stop"


class TestFailing:
    @pytest.mark.parametrize(
        ("exc", "code", "transient"),
        [
            (_status_error(429), "llm_error", True),
            (_status_error(500), "service_unavailable", True),
            (_status_error(503), "service_unavailable", True),
            (_status_error(403), "auth_denied", False),
            (_status_error(401), "auth_denied", False),
            (_status_error(404), "llm_error", False),
            (_status_error(400), "llm_error", False),
            (anthropic.APITimeoutError(request=_REQUEST), "timeout", True),
            (anthropic.APIConnectionError(request=_REQUEST), "service_unavailable", True),
        ],
    )
    def test_a_failure_before_the_first_word_is_one_error_event(
        self, exc: BaseException, code: str, transient: bool
    ) -> None:
        events, _ = _run(_FakeStream(["never"], fail_with=exc, fail_before=0))

        assert events == [
            StreamEvent(
                finish_reason="error",
                error_code=code,
                error_message=type(exc).__name__,
                transient=transient,
            )
        ]

    def test_a_failure_after_words_ends_that_answer_with_an_error(self) -> None:
        events, _ = _run(
            _FakeStream(["Half ", "an ", "answer"], fail_with=_status_error(503), fail_before=2)
        )

        assert [e.delta for e in events[:-1]] == ["Half ", "an "]
        assert events[-1].finish_reason == "error"
        assert events[-1].transient

    def test_the_log_names_the_failure_and_never_the_conversation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        prompt = f"Be brief.{PATIENT_CONTEXT_DELIMITER}{SECRET}"
        with caplog.at_level(logging.DEBUG):
            _run(
                _FakeStream(["x"], fail_with=_status_error(503), fail_before=0),
                system_prompt=prompt,
                new_user_text=SECRET,
            )

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "HTTP 503" in logged
        assert "claude-sonnet-4-6" in logged
        assert SECRET not in logged
        assert "service said no" not in logged


_CAPTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "anthropic_vertex_stream.json").read_text(
        encoding="utf-8"
    )
)


def _through_the_real_sdk(
    capture: str, *, model: str
) -> tuple[list[StreamEvent], list[httpx.Request]]:
    """Stream ``model`` with the real SDK reading a captured response off the wire.

    Only the network is replaced: the SDK builds the request, parses the
    server-sent events and assembles the final message exactly as it does
    against Vertex.
    """
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_CAPTURES[capture]["sse"].encode(),
        )

    async def collect() -> list[StreamEvent]:
        client = AsyncAnthropicVertex(
            project_id="test-project",
            region="global",
            access_token="test-token",  # noqa: S106 — a placeholder; the network is mocked
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        gateway = AnthropicVertexChatLLMGateway(client=client)
        return [
            event
            async for event in gateway.stream_completion(
                model=model,
                system_prompt=f"You are terse.{PATIENT_CONTEXT_DELIMITER}Context: the sky is blue.",
                prior_turns=[
                    UserAssistantTurn("assistant", "Earlier I said hello."),
                    UserAssistantTurn("user", "hi"),
                ],
                new_user_text="Per the context, what colour is the sky? One word.",
                max_output_tokens=_CAPTURES[capture]["max_tokens"],
                temperature=0.4,
            )
        ]

    return asyncio.run(collect()), seen


class TestCapturedStreams:
    """Real responses from Claude on Vertex, read by the real SDK.

    ``fixtures/anthropic_vertex_stream.json`` holds streams captured from
    ``streamRawPredict`` with only ids and the reasoning signature replaced.
    A fake that replays what we imagine the SDK does can only agree with us;
    these can notice the SDK or the endpoint changing underneath.
    """

    def test_a_plain_answer_streams_and_finishes(self) -> None:
        events, _ = _through_the_real_sdk("sonnet_answers", model="anthropic:claude-sonnet-5-5")

        assert events == [
            StreamEvent(delta="Blue."),
            StreamEvent(finish_reason="stop", output_tokens=6),
        ]

    def test_reasoning_before_the_answer_shows_the_reader_only_the_answer(self) -> None:
        """Opus 5.5 returns a thinking block first; none of it reaches the reader."""
        events, _ = _through_the_real_sdk(
            "opus_reasons_then_answers", model="anthropic:claude-opus-5-5"
        )

        assert [e.delta for e in events if e.delta] == ["Blue", "."]
        # The reasoning is billed as output: 65 tokens for a two-token answer.
        assert events[-1] == StreamEvent(finish_reason="stop", output_tokens=65)

    def test_a_budget_spent_on_reasoning_ends_with_no_words_and_length(self) -> None:
        """Out of tokens before any text is "too long", not a refusal."""
        events, _ = _through_the_real_sdk(
            "opus_runs_out_of_tokens", model="anthropic:claude-opus-5-5"
        )

        assert events == [StreamEvent(finish_reason="length", output_tokens=6)]

    def test_the_request_on_the_wire_names_the_model_and_leaves_out_temperature(self) -> None:
        _, seen = _through_the_real_sdk("sonnet_answers", model="anthropic:claude-sonnet-5-5")

        request = seen[0]
        body = json.loads(request.content)
        assert request.url.path.endswith("/models/claude-sonnet-5-5:streamRawPredict")
        assert body["stream"] is True
        assert "temperature" not in body
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in body["system"][1]


class _SpanProbe:
    def __init__(self) -> None:
        self.usage: dict[str, Any] = {}

    def set_token_usage(self, **usage: Any) -> None:
        self.usage = usage


class TestUsage:
    def test_cache_reads_and_writes_both_count_toward_the_prompt(self) -> None:
        """Anthropic reports both apart from input_tokens; only the reads came from cache."""
        span = _SpanProbe()
        usage = _usage(input_tokens=20, output_tokens=9, cache_read=100)
        usage.cache_creation_input_tokens = 50

        module._record_usage(span, usage, 9)  # type: ignore[arg-type]

        assert span.usage == {"prompt_tokens": 170, "completion_tokens": 9, "cached_tokens": 100}

    def test_a_turn_with_no_cache_activity_reports_the_plain_prompt(self) -> None:
        span = _SpanProbe()
        usage = _usage(input_tokens=20, output_tokens=9)
        usage.cache_creation_input_tokens = None

        module._record_usage(span, usage, 9)  # type: ignore[arg-type]

        assert span.usage == {"prompt_tokens": 20, "completion_tokens": 9, "cached_tokens": None}


class TestClient:
    def test_one_gateway_serves_the_process(self) -> None:
        assert get_anthropic_vertex_chat_llm_gateway() is get_anthropic_vertex_chat_llm_gateway()

    def test_a_client_is_built_for_each_event_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A job that runs asyncio.run twice must not reuse a client from the closed loop."""
        built: list[object] = []

        def build(**_: Any) -> object:
            built.append(object())
            return built[-1]

        monkeypatch.setattr(module, "async_anthropic_vertex_client", build)
        gateway = AnthropicVertexChatLLMGateway()

        async def get() -> object:
            first = gateway._get_client()
            assert gateway._get_client() is first  # same loop: reused
            return first

        a = asyncio.run(get())
        b = asyncio.run(get())

        assert a is not b
        assert len(built) == 2
