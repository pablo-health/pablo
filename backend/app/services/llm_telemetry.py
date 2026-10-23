# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Content-free tracing for LLM calls.

Emits one OpenInference-shaped OpenTelemetry span per LLM call carrying
*only* metadata — model, token counts, latency, error class, and the
request-scoped contextvars (request_id / user_id / tenant_id /
route_template). Prompt and response text are never recorded.

The same metadata is also written as one ``llm_call`` log record per
call (:func:`_log_usage`), labelled with the AI feature the call served
(:func:`llm_feature`). The span is for a trace viewer; the record is what
a cost report sums, grouped by feature and model, and it needs no
collector to exist.

The omission is enforced by construction, not by discipline. The public
surface — :class:`LLMSpanRequest` and :class:`LLMSpanRecorder` — has no
field or method that accepts message content. A developer adding a new
call site physically cannot attach a prompt or completion to the span,
so a later edit can't quietly start leaking content into the telemetry
backend.

Why hand-built OpenInference attributes rather than the OpenInference
auto-instrumentors: the Arize-maintained instrumentors patch the SDK and
capture ``input.value`` / ``output.value`` (the prompt and the
completion) by default — exactly what we must not record. We instead set
the same OpenInference semantic-convention attributes ourselves, so
Phoenix (and any other OTel backend) renders these as LLM spans while the
content keys are simply never written.

Exporter wiring is config-driven (:func:`init_llm_tracing`). With no
collector endpoint configured the global tracer stays the OTel no-op, so
spans cost almost nothing and a deployment ships with tracing off until
``PHOENIX_COLLECTOR_ENDPOINT`` is pointed at an OTLP/HTTP collector
(Phoenix, Honeycomb, Tempo, Cloud Trace, …). Switching backends is an
endpoint change, never a re-instrumentation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from openinference.semconv.trace import (
    DocumentAttributes,
    OpenInferenceSpanKindValues,
    SpanAttributes,
)
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ..logging_config import (
    current_log_tenant_id,
    current_log_user_id,
    request_id_var,
    route_template_var,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import Span

    from ..settings import Settings

logger = logging.getLogger(__name__)

_TRACER_NAME = "pablo.llm"

# Pablo-namespaced attributes that sit alongside the OpenInference ones.
# request_id / route_template are populated by the request-context
# middleware; user_id / tenant_id by the auth dependency chain. Grouping
# them under a ``pablo.`` prefix keeps them distinct from the standard
# OpenInference keys in the Phoenix UI.
_ATTR_OPERATION = "pablo.llm_operation"
_ATTR_REQUEST_ID = "pablo.request_id"
_ATTR_TENANT_ID = "pablo.tenant_id"
_ATTR_ROUTE_TEMPLATE = "pablo.route_template"
_ATTR_PROMPT_TEMPLATE_ID = "pablo.prompt_template_id"
_ATTR_ERROR_CLASS = "pablo.error_class"
_ATTR_LATENCY_MS = "pablo.latency_ms"
_ATTR_RETRIEVAL_OPERATION = "pablo.retrieval_operation"
_ATTR_RETRIEVAL_DOC_COUNT = "pablo.retrieval.document_count"
_ATTR_CONTEXT_TOKENS_EST = "pablo.retrieval.context_tokens_est"
_ATTR_FEATURE = "pablo.llm_feature"
_ATTR_CACHED_TOKENS = "pablo.llm_cached_tokens"
_ATTR_THOUGHTS_TOKENS = "pablo.llm_thoughts_tokens"

# The message of the per-call usage record. A log filter keys on it, so it
# is a fixed token rather than prose.
USAGE_LOG_MESSAGE = "llm_call"

# The AI feature a call is made for (an ``AIFeature`` key), set by the
# gateway or service that knows it and read by every span opened beneath.
# A contextvar rather than a parameter so the label survives the hedge's
# worker threads and the provider gateways in between, none of which need
# to know about it.
llm_feature_var: ContextVar[str | None] = ContextVar("llm_feature", default=None)


@contextmanager
def llm_feature(feature: str | None) -> Iterator[None]:
    """Label every LLM span and usage record opened inside as ``feature``.

    ``None`` leaves whatever label is already set, so a gateway built
    without a feature does not blank one a caller set further out.
    """
    if feature is None:
        yield
        return
    token = llm_feature_var.set(feature)
    try:
        yield
    finally:
        llm_feature_var.reset(token)


@dataclass(frozen=True)
class LLMSpanRequest:
    """Content-free descriptor of an LLM call.

    Note what's *absent*: no field for the prompt, the system
    instruction, the message history, or the completion. That's the
    point — the type cannot carry content, so a span built from it
    cannot leak any.

    ``operation`` is a short, low-cardinality verb (``"chat"``,
    ``"structured"``, ``"ehr_navigation"``, ``"embedding"``) used both
    for the span name and a queryable attribute. ``provider`` maps to the
    OpenInference ``llm.system`` dimension.
    """

    operation: str
    model: str
    prompt_template_id: str | None = None
    provider: str = "google"


class LLMSpanRecorder:
    """Handle for attaching *metadata only* to an in-flight LLM span.

    Exposes token counts and an error-class setter and nothing else. It
    deliberately offers no method that accepts message text — see the
    module docstring for why content omission lives here rather than at
    the call site.
    """

    __slots__ = (
        "_cached_tokens",
        "_completion_tokens",
        "_error_class",
        "_prompt_tokens",
        "_span",
        "_thoughts_tokens",
        "_total_tokens",
    )

    def __init__(self, span: Span) -> None:
        self._span = span
        # Kept beside the span so the usage record written when the span
        # ends can carry them; a no-op span (tracing off) keeps nothing.
        # Private on purpose: the public surface stays the two setters, so
        # the no-content-sink guard in the tests keeps meaning what it says.
        self._prompt_tokens: int | None = None
        self._completion_tokens: int | None = None
        self._total_tokens: int | None = None
        self._cached_tokens: int | None = None
        self._thoughts_tokens: int | None = None
        self._error_class: str | None = None

    def set_token_usage(
        self,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cached_tokens: int | None = None,
        thoughts_tokens: int | None = None,
    ) -> None:
        """Record token counts on the span. Each is skipped when ``None``.

        ``cached_tokens`` is the part of the prompt the provider served from
        its cache (billed at a lower rate); ``thoughts_tokens`` is reasoning
        the model spent before answering.
        """
        if prompt_tokens is not None:
            self._prompt_tokens = prompt_tokens
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, prompt_tokens)
        if completion_tokens is not None:
            self._completion_tokens = completion_tokens
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, completion_tokens)
        if total_tokens is not None:
            self._total_tokens = total_tokens
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_TOTAL, total_tokens)
        if cached_tokens is not None:
            self._cached_tokens = cached_tokens
            self._span.set_attribute(_ATTR_CACHED_TOKENS, cached_tokens)
        if thoughts_tokens is not None:
            self._thoughts_tokens = thoughts_tokens
            self._span.set_attribute(_ATTR_THOUGHTS_TOKENS, thoughts_tokens)

    def set_error_class(self, error_class: str) -> None:
        """Record a non-PHI error classifier (an exception/type name)."""
        self._error_class = error_class
        self._span.set_attribute(_ATTR_ERROR_CLASS, error_class)


def usage_tokens(usage: object) -> tuple[int | None, int | None, int | None]:
    """Pull ``(prompt, completion, total)`` token counts off a google-genai
    ``usage_metadata`` object. Returns ``None`` for any field the SDK didn't
    populate (embeddings, for instance, report only a prompt/total count).
    """
    if usage is None:
        return (None, None, None)
    prompt = getattr(usage, "prompt_token_count", None)
    completion = getattr(usage, "candidates_token_count", None)
    total = getattr(usage, "total_token_count", None)
    return (prompt, completion, total)


def usage_detail_tokens(usage: object) -> tuple[int | None, int | None]:
    """Pull ``(cached, thoughts)`` token counts off a google-genai
    ``usage_metadata`` object; ``None`` where the SDK didn't populate one.

    Counts only. Pass the result to ``set_token_usage`` as ``cached_tokens``
    and ``thoughts_tokens``.
    """
    if usage is None:
        return (None, None)
    cached = getattr(usage, "cached_content_token_count", None)
    thoughts = getattr(usage, "thoughts_token_count", None)
    return (cached, thoughts)


def _span_kind(operation: str) -> str:
    if operation == "embedding":
        return OpenInferenceSpanKindValues.EMBEDDING.value
    return OpenInferenceSpanKindValues.LLM.value


def _set_context_attributes(span: Span) -> None:
    """Merge the request-scoped contextvars onto the span (PHI-free)."""
    if (rid := request_id_var.get()) is not None:
        span.set_attribute(_ATTR_REQUEST_ID, rid)
    if (uid := current_log_user_id()) is not None:
        span.set_attribute(SpanAttributes.USER_ID, uid)
    if (tid := current_log_tenant_id()) is not None:
        span.set_attribute(_ATTR_TENANT_ID, tid)
    if (tmpl := route_template_var.get()) is not None:
        span.set_attribute(_ATTR_ROUTE_TEMPLATE, tmpl)
    if (feature := llm_feature_var.get()) is not None:
        span.set_attribute(_ATTR_FEATURE, feature)


def _log_usage(request: LLMSpanRequest, recorder: LLMSpanRecorder, latency_ms: float) -> None:
    """Write the per-call usage record: what was called, for which feature,
    and what it cost in tokens. Nothing from the prompt or the completion.

    This is the record a cost report sums, so it is written whether or not
    a trace collector is configured: the span is for a trace viewer, this
    line is for the log, and a deployment usually has the second without
    the first. The request-scoped ids (request, user, tenant) are added by
    the JSON formatter, like on every other line.
    """
    extra: dict[str, object] = {
        "llm_operation": request.operation,
        "llm_model": request.model,
        "llm_provider": request.provider,
        "llm_latency_ms": latency_ms,
    }
    if (feature := llm_feature_var.get()) is not None:
        extra["llm_feature"] = feature
    if recorder._prompt_tokens is not None:
        extra["llm_prompt_tokens"] = recorder._prompt_tokens
    if recorder._completion_tokens is not None:
        extra["llm_completion_tokens"] = recorder._completion_tokens
    if recorder._total_tokens is not None:
        extra["llm_total_tokens"] = recorder._total_tokens
    if recorder._cached_tokens is not None:
        extra["llm_cached_tokens"] = recorder._cached_tokens
    if recorder._thoughts_tokens is not None:
        extra["llm_thoughts_tokens"] = recorder._thoughts_tokens
    if recorder._error_class is not None:
        extra["llm_error_class"] = recorder._error_class
    logger.info(USAGE_LOG_MESSAGE, extra=extra)


@contextmanager
def llm_span(request: LLMSpanRequest) -> Iterator[LLMSpanRecorder]:
    """Open a content-free OpenInference span for one LLM call.

    Usage::

        with llm_span(LLMSpanRequest(operation="structured", model=model)) as rec:
            response = client.models.generate_content(...)
            rec.set_token_usage(*usage_tokens(response.usage_metadata))

    Latency is recorded automatically on exit, and any exception raised
    inside the block tags the span with its class name and an ERROR
    status before propagating (the exception message is never recorded,
    only the type name). When no collector is configured the underlying
    tracer is a no-op and this is nearly free.
    """
    tracer = trace.get_tracer(_TRACER_NAME)
    start = time.perf_counter()
    # ``start_span``, deliberately not ``start_as_current_span``. The latter
    # attaches the span to the ambient context on entry and detaches it on
    # exit. Under a streaming gateway the exit is the async generator's
    # finalizer, which runs in whichever task closes the generator -- a
    # consumer that breaks out of the stream early, or the event loop's
    # garbage collector -- so the detach token belongs to another Context and
    # OpenTelemetry logs "Failed to detach context" at ERROR for a request
    # that already succeeded. Nothing under this span reads the current span,
    # so not making it current loses nothing; the span is ended by hand in
    # the outer ``finally``. Skipping ``start_as_current_span`` also skips
    # its default exception event, which would record the exception message;
    # only the class name belongs on a content-free span.
    span = tracer.start_span(f"llm.{request.operation}")
    try:
        span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, _span_kind(request.operation))
        span.set_attribute(SpanAttributes.LLM_MODEL_NAME, request.model)
        span.set_attribute(SpanAttributes.LLM_SYSTEM, request.provider)
        span.set_attribute(_ATTR_OPERATION, request.operation)
        if request.prompt_template_id is not None:
            span.set_attribute(_ATTR_PROMPT_TEMPLATE_ID, request.prompt_template_id)
        _set_context_attributes(span)
        recorder = LLMSpanRecorder(span)
        try:
            yield recorder
        except (GeneratorExit, asyncio.CancelledError):
            # Client disconnect / task cancellation — control flow, not a
            # failure. A streaming caller closing the iterator early throws
            # GeneratorExit in here; tagging it ERROR would pollute error
            # dashboards with normal disconnects. Re-raise untagged.
            raise
        except BaseException as exc:
            # Tag the span and re-raise — we never swallow the caller's error.
            recorder._error_class = type(exc).__name__
            span.set_attribute(_ATTR_ERROR_CLASS, type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
            span.set_attribute(_ATTR_LATENCY_MS, latency_ms)
            _log_usage(request, recorder, latency_ms)
    finally:
        span.end()


# ---------------------------------------------------------------------------
# Retrieval tracing (content-free, same discipline as the LLM span above)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievedDocumentRef:
    """Content-free reference to one retrieved context document.

    Mirrors :class:`LLMSpanRequest`'s discipline: the type has *no* field
    for the document's text — only its id, its source, and a token
    estimate — so a retrieval span built from these refs cannot leak chart
    content into the telemetry backend. ``document_id`` is the chart id
    (note id / patient-document id) where one exists, else the source key.
    """

    document_id: str
    source: str | None = None
    tokens_est: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class RetrievalSpanRecorder:
    """Handle for attaching *metadata only* to an in-flight retrieval span.

    Like :class:`LLMSpanRecorder`, it offers no method that accepts
    document text — only the content-free :class:`RetrievedDocumentRef`
    list and an aggregate token estimate.
    """

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def set_documents(self, documents: Sequence[RetrievedDocumentRef]) -> None:
        """Record which documents were retrieved (ids + counts; never text)."""
        self._span.set_attribute(_ATTR_RETRIEVAL_DOC_COUNT, len(documents))
        docs_prefix = SpanAttributes.RETRIEVAL_DOCUMENTS
        for i, doc in enumerate(documents):
            self._span.set_attribute(
                f"{docs_prefix}.{i}.{DocumentAttributes.DOCUMENT_ID}", doc.document_id
            )
            metadata: dict[str, object] = dict(doc.metadata)
            if doc.source is not None:
                metadata.setdefault("source", doc.source)
            if doc.tokens_est is not None:
                metadata.setdefault("tokens_est", doc.tokens_est)
            if metadata:
                self._span.set_attribute(
                    f"{docs_prefix}.{i}.{DocumentAttributes.DOCUMENT_METADATA}",
                    json.dumps(metadata, sort_keys=True, default=str),
                )

    def set_context_tokens(self, total_tokens_est: int) -> None:
        """Record the total estimated token size of the assembled context."""
        self._span.set_attribute(_ATTR_CONTEXT_TOKENS_EST, total_tokens_est)


@contextmanager
def retrieval_span(*, operation: str) -> Iterator[RetrievalSpanRecorder]:
    """Open a content-free OpenInference RETRIEVER span for one context assembly.

    The retrieval counterpart to :func:`llm_span`: it makes *which* chart
    sources fed a turn queryable in any OTLP backend, alongside the LLM
    span the gateway already emits. Same guarantees — metadata only,
    latency on exit, error class (not message) on exception, and a no-op
    tracer when no collector is configured.

    Usage::

        with retrieval_span(operation="chat_context") as rec:
            bundle = assemble_context_bundle(...)
            rec.set_documents([RetrievedDocumentRef(document_id=...), ...])
            rec.set_context_tokens(bundle.total_tokens_est)
    """
    tracer = trace.get_tracer(_TRACER_NAME)
    start = time.perf_counter()
    with tracer.start_as_current_span(f"retrieval.{operation}") as span:
        span.set_attribute(
            SpanAttributes.OPENINFERENCE_SPAN_KIND,
            OpenInferenceSpanKindValues.RETRIEVER.value,
        )
        span.set_attribute(_ATTR_RETRIEVAL_OPERATION, operation)
        _set_context_attributes(span)
        recorder = RetrievalSpanRecorder(span)
        try:
            yield recorder
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except BaseException as exc:
            span.set_attribute(_ATTR_ERROR_CLASS, type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
            span.set_attribute(_ATTR_LATENCY_MS, latency_ms)


# ---------------------------------------------------------------------------
# Exporter wiring (config-driven; no-op until an endpoint is configured)
# ---------------------------------------------------------------------------

_init_lock = threading.Lock()
# Single-element holder used as an idempotency latch (avoids a module-level
# ``global``, matching the gateway-singleton pattern in this package).
_provider_installed: list[bool] = []


def _endpoint_audience(endpoint: str) -> str:
    """Return the ``scheme://host`` origin of an OTLP endpoint.

    Used as the ID-token audience: a Cloud Run service expects a token
    whose ``aud`` is its base URL, not the ``/v1/traces`` path.
    """
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def _build_resource(settings: Settings) -> Resource:
    """Build the OTel resource for exported spans.

    ``service.name`` always; ``openinference.project.name`` only when
    ``llm_trace_project`` is set — that attribute is what Phoenix uses to
    route spans into a project (absent it, they land in ``default``).
    """
    from opentelemetry.sdk.resources import Resource

    attributes: dict[str, str] = {"service.name": settings.llm_trace_service_name}
    if settings.llm_trace_project:
        attributes["openinference.project.name"] = settings.llm_trace_project
    return Resource.create(attributes)


def init_llm_tracing(settings: Settings) -> None:
    """Install the global tracer provider + OTLP/HTTP exporter, if configured.

    No-op when ``phoenix_collector_endpoint`` is unset — the global
    tracer stays the OTel default no-op so self-hosted installs run with
    LLM tracing off and pay nothing. Idempotent: safe to call more than
    once (only the first call installs a provider).

    When ``llm_trace_use_id_token`` is set (the default), exports are
    authenticated with a Google-minted ID token whose audience is the
    endpoint origin — the posture for a Cloud Run-hosted collector behind
    ``roles/run.invoker``. Turn it off for a collector that authenticates
    via static ``OTEL_EXPORTER_OTLP_*`` headers instead.
    """
    endpoint = settings.phoenix_collector_endpoint.strip()
    if not endpoint:
        logger.debug("LLM tracing disabled: phoenix_collector_endpoint is unset")
        return

    with _init_lock:
        if _provider_installed:
            return

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        session = None
        if settings.llm_trace_use_id_token:
            session = _build_id_token_session(_endpoint_audience(endpoint))

        exporter = OTLPSpanExporter(endpoint=endpoint, session=session)
        provider = TracerProvider(resource=_build_resource(settings))
        # BatchSpanProcessor exports off-thread, so span.end() never blocks the
        # request path on the collector. Keep it Batch (never SimpleSpanProcessor)
        # — a synchronous exporter would couple request latency to the
        # collector's responsiveness, including its cold-start when it scales to
        # zero.
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _provider_installed.append(True)
        # Logs the OTLP exporter endpoint + an auth bool — no secret value.
        # nosemgrep
        logger.info(
            "LLM tracing enabled: exporting OpenInference spans to %s (id_token_auth=%s)",
            endpoint,
            settings.llm_trace_use_id_token,
        )


def _build_id_token_session(audience: str) -> object:
    """Return an auto-refreshing ``AuthorizedSession`` for ID-token auth.

    Uses the runtime metadata server (Cloud Run / GCE), so it only
    functions in a deployed GCP environment — which is the only place a
    collector endpoint is configured. ``AuthorizedSession`` refreshes the
    token before expiry on its own, so the long-lived exporter never
    sends a stale bearer.
    """
    from google.auth.compute_engine import IDTokenCredentials
    from google.auth.transport.requests import AuthorizedSession, Request

    credentials = IDTokenCredentials(Request(), target_audience=audience)
    return AuthorizedSession(credentials)


__all__ = [
    "USAGE_LOG_MESSAGE",
    "LLMSpanRecorder",
    "LLMSpanRequest",
    "RetrievalSpanRecorder",
    "RetrievedDocumentRef",
    "init_llm_tracing",
    "llm_feature",
    "llm_feature_var",
    "llm_span",
    "retrieval_span",
    "usage_tokens",
]
