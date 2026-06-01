# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for content-free LLM tracing.

The load-bearing guarantee: a real LLM call, routed through the gateways,
produces an OpenInference span carrying token counts and latency but
*zero* prompt or response content. These tests exercise the actual
``GeminiStructuredLLMGateway`` / ``GeminiChatLLMGateway`` code paths
against a fake ``google.genai`` client (no network, no credentials) and
assert on the spans captured by an in-memory exporter.

The prompts and responses below contain deliberately distinctive marker
strings; the core assertion is that none of them appear anywhere in the
emitted span attributes.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from openinference.semconv.trace import (
    DocumentAttributes,
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app.services.chat_llm_gateway import GeminiChatLLMGateway, StreamEvent
from backend.app.services.llm_telemetry import (
    LLMSpanRecorder,
    LLMSpanRequest,
    RetrievalSpanRecorder,
    RetrievedDocumentRef,
    _build_resource,
    llm_span,
    retrieval_span,
)
from backend.app.services.structured_llm_gateway import GeminiStructuredLLMGateway

if TYPE_CHECKING:
    from collections.abc import Iterator

# Marker strings that must never reach the telemetry backend.
_SECRET_SYSTEM = "SYSTEM-PROMPT-marker-do-not-leak"
_SECRET_USER = "USER-PROMPT-marker-patient-Jane-Roe"
_SECRET_RESPONSE = "RESPONSE-marker-SOAP-note-body"

# OpenInference content attribute keys that must never be set.
_CONTENT_KEYS = (
    SpanAttributes.INPUT_VALUE,
    SpanAttributes.OUTPUT_VALUE,
    SpanAttributes.LLM_INPUT_MESSAGES,
    SpanAttributes.LLM_OUTPUT_MESSAGES,
)


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Install an in-memory span exporter and yield it.

    Adds a ``SimpleSpanProcessor`` to the active tracer provider (creating
    one if the global is still the default no-op), so spans built by
    ``llm_span`` land in the exporter synchronously. The exporter is
    cleared on entry so each test sees only its own spans.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    yield exporter
    exporter.clear()


def _assert_no_content(span_attrs: dict[str, Any]) -> None:
    """Fail if any marker string or content attribute key is present."""
    for key in _CONTENT_KEYS:
        assert key not in span_attrs, f"content attribute {key!r} leaked onto span"
    haystack = " ".join(f"{k}={v}" for k, v in span_attrs.items())
    for secret in (_SECRET_SYSTEM, _SECRET_USER, _SECRET_RESPONSE):
        assert secret not in haystack, f"content marker {secret!r} leaked onto span"


# ---------------------------------------------------------------------------
# Fakes for the google.genai client surface
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_token_count = prompt
        self.candidates_token_count = completion
        self.total_token_count = total


class _FakeCandidate:
    finish_reason = "STOP"


class _FakeStructuredResponse:
    def __init__(self) -> None:
        self.text = f'{{"note": "{_SECRET_RESPONSE}"}}'
        self.usage_metadata = _FakeUsage(prompt=31, completion=12, total=43)
        self.candidates = [_FakeCandidate()]


class _FakeStructuredModels:
    def generate_content(self, **kwargs: Any) -> _FakeStructuredResponse:
        return _FakeStructuredResponse()


class _FakeStructuredClient:
    def __init__(self) -> None:
        self.models = _FakeStructuredModels()


class _FakeChunk:
    """One streamed chunk. The final chunk carries usage + finish reason."""

    def __init__(
        self,
        *,
        text: str = "",
        finish: bool = False,
        completion_tokens: int | None = None,
    ) -> None:
        self.text = text
        self.candidates = [_FakeCandidate()] if finish else []
        self.usage_metadata = (
            _FakeUsage(prompt=0, completion=completion_tokens, total=completion_tokens)
            if completion_tokens is not None
            else None
        )


class _FakeAioChatModels:
    """Mirrors ``client.aio.models``: an awaitable returning an async iterator."""

    async def generate_content_stream(self, **kwargs: Any) -> Any:
        async def _gen() -> Any:
            for chunk in (
                _FakeChunk(text=_SECRET_RESPONSE[:10]),
                _FakeChunk(text=_SECRET_RESPONSE[10:]),
                _FakeChunk(finish=True, completion_tokens=9),
            ):
                yield chunk

        return _gen()


class _FakeAio:
    def __init__(self) -> None:
        self.models = _FakeAioChatModels()


class _FakeChatClient:
    def __init__(self) -> None:
        self.aio = _FakeAio()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStructuredGatewayTracing:
    def test_emits_content_free_span_with_tokens_and_latency(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        gw = GeminiStructuredLLMGateway()
        gw._client = _FakeStructuredClient()

        result = gw.complete_structured(
            model="gemini-2.5-pro",
            system_prompt=_SECRET_SYSTEM,
            user_prompt=_SECRET_USER,
            response_schema={"type": "object", "properties": {"note": {"type": "string"}}},
            max_output_tokens=256,
        )
        assert result.data == {"note": _SECRET_RESPONSE}

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        attrs = dict(span.attributes or {})

        assert span.name == "llm.structured"
        assert (
            attrs[SpanAttributes.OPENINFERENCE_SPAN_KIND] == OpenInferenceSpanKindValues.LLM.value
        )
        assert attrs[SpanAttributes.LLM_MODEL_NAME] == "gemini-2.5-pro"
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 31
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == 12
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_TOTAL] == 43
        assert attrs["pablo.latency_ms"] >= 0.0
        assert attrs["pablo.llm_operation"] == "structured"
        _assert_no_content(attrs)

    def test_failed_call_tags_error_class_without_content(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        class _BoomModels:
            def generate_content(self, **kwargs: Any) -> Any:
                raise ConnectionError(_SECRET_USER)  # message carries a marker

        class _BoomClient:
            models = _BoomModels()

        gw = GeminiStructuredLLMGateway()
        gw._client = _BoomClient()

        with pytest.raises(RuntimeError):
            gw.complete_structured(
                model="gemini-2.5-pro",
                system_prompt=_SECRET_SYSTEM,
                user_prompt=_SECRET_USER,
                response_schema={"type": "object"},
                max_output_tokens=64,
            )

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        # The wrapper converts the SDK error to RuntimeError before it
        # propagates out of the span block.
        assert attrs["pablo.error_class"] == "RuntimeError"
        _assert_no_content(attrs)


class TestChatGatewayTracing:
    def test_streaming_span_records_completion_tokens_no_content(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        gw = GeminiChatLLMGateway()
        gw._client = _FakeChatClient()

        async def _drain() -> list[StreamEvent]:
            events: list[StreamEvent] = []
            async for event in gw.stream_completion(
                model="gemini-2.5-flash-lite",
                system_prompt=_SECRET_SYSTEM,
                prior_turns=[],
                new_user_text=_SECRET_USER,
                max_output_tokens=128,
            ):
                events.append(event)
            return events

        events = asyncio.run(_drain())
        # Sanity: the stream actually carried response text through deltas.
        assert any(e.delta for e in events)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        assert spans[0].name == "llm.chat"
        assert attrs[SpanAttributes.LLM_MODEL_NAME] == "gemini-2.5-flash-lite"
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_COMPLETION] == 9
        assert attrs["pablo.latency_ms"] >= 0.0
        _assert_no_content(attrs)


class TestLLMSpanBuilder:
    def test_recorder_exposes_no_content_setter(self) -> None:
        """The recorder API surface must not offer any content sink."""
        public = {name for name in dir(LLMSpanRecorder) if not name.startswith("_")}
        assert public == {"set_token_usage", "set_error_class"}

    def test_request_has_no_content_fields(self) -> None:
        names = {f.name for f in fields(LLMSpanRequest)}
        assert names == {"operation", "model", "prompt_template_id", "provider"}

    def test_error_inside_block_tags_error_class(self, span_exporter: InMemorySpanExporter) -> None:
        with (
            pytest.raises(ValueError, match="boom"),
            llm_span(LLMSpanRequest(operation="chat", model="m")),
        ):
            raise ValueError("boom")

        attrs = dict(span_exporter.get_finished_spans()[0].attributes or {})
        assert attrs["pablo.error_class"] == "ValueError"

    def test_client_disconnect_is_not_tagged_error(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """Closing a streaming consumer early throws GeneratorExit into the
        span; that's a disconnect, not a failure, so it must not be tagged."""

        def _streaming_consumer() -> Any:
            with llm_span(LLMSpanRequest(operation="chat", model="m")):
                yield "first-chunk"
                yield "second-chunk"  # never reached — closed after first

        gen = _streaming_consumer()
        next(gen)  # enter the span, suspend at the first yield
        gen.close()  # raises GeneratorExit into the span block

        span_attrs = dict(span_exporter.get_finished_spans()[0].attributes or {})
        assert "pablo.error_class" not in span_attrs
        assert span_attrs["pablo.latency_ms"] >= 0.0

    def test_span_builder_records_context_free_metadata(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        with llm_span(LLMSpanRequest(operation="embedding", model="text-embedding-004")) as rec:
            rec.set_token_usage(prompt_tokens=5, total_tokens=5)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        attrs = dict(spans[0].attributes or {})
        assert spans[0].name == "llm.embedding"
        assert (
            attrs[SpanAttributes.OPENINFERENCE_SPAN_KIND]
            == OpenInferenceSpanKindValues.EMBEDDING.value
        )
        assert attrs[SpanAttributes.LLM_TOKEN_COUNT_PROMPT] == 5


class TestRetrievalSpan:
    """The retrieval span mirrors the LLM span's content-free discipline."""

    def test_recorder_exposes_no_content_setter(self) -> None:
        public = {name for name in dir(RetrievalSpanRecorder) if not name.startswith("_")}
        assert public == {"set_documents", "set_context_tokens"}

    def test_ref_has_no_text_field(self) -> None:
        names = {f.name for f in fields(RetrievedDocumentRef)}
        assert names == {"document_id", "source", "tokens_est", "metadata"}
        assert "text" not in names
        assert "content" not in names

    def test_emits_retriever_span_with_ids_and_no_content(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        with retrieval_span(operation="chat_context") as rec:
            rec.set_documents(
                [
                    RetrievedDocumentRef(
                        document_id="note-1", source="progress_notes_recent", tokens_est=42
                    ),
                    RetrievedDocumentRef(
                        document_id="doc-9", source="patient_documents", tokens_est=7
                    ),
                ]
            )
            rec.set_context_tokens(49)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        attrs = dict(span.attributes or {})
        assert span.name == "retrieval.chat_context"
        assert (
            attrs[SpanAttributes.OPENINFERENCE_SPAN_KIND]
            == OpenInferenceSpanKindValues.RETRIEVER.value
        )
        assert attrs["pablo.retrieval.document_count"] == 2
        assert attrs["pablo.retrieval.context_tokens_est"] == 49
        assert attrs[
            f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.0.{DocumentAttributes.DOCUMENT_ID}"
        ] == ("note-1")
        assert attrs[
            f"{SpanAttributes.RETRIEVAL_DOCUMENTS}.1.{DocumentAttributes.DOCUMENT_ID}"
        ] == ("doc-9")
        assert attrs["pablo.latency_ms"] >= 0.0
        # By construction the span carries no document text — assert the
        # OpenInference content key is never set on any document.
        content_suffix = f".{DocumentAttributes.DOCUMENT_CONTENT}"
        assert not any(key.endswith(content_suffix) for key in attrs)
        _assert_no_content(attrs)

    def test_error_inside_block_tags_error_class(self, span_exporter: InMemorySpanExporter) -> None:
        with pytest.raises(ValueError, match="boom"), retrieval_span(operation="chat_context"):
            raise ValueError("boom")

        attrs = dict(span_exporter.get_finished_spans()[0].attributes or {})
        assert attrs["pablo.error_class"] == "ValueError"


class TestResourceProject:
    """The Phoenix project is set via the ``openinference.project.name``
    resource attribute when ``llm_trace_project`` is configured."""

    def test_project_set_when_configured(self) -> None:
        settings = SimpleNamespace(
            llm_trace_service_name="pablo-backend", llm_trace_project="pablo-prod"
        )
        attrs = dict(_build_resource(settings).attributes)  # type: ignore[arg-type]
        assert attrs["openinference.project.name"] == "pablo-prod"
        assert attrs["service.name"] == "pablo-backend"

    def test_project_absent_when_unset(self) -> None:
        settings = SimpleNamespace(llm_trace_service_name="pablo-backend", llm_trace_project="")
        attrs = dict(_build_resource(settings).attributes)  # type: ignore[arg-type]
        assert "openinference.project.name" not in attrs
