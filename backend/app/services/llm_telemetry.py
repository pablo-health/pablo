# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Content-free tracing for LLM calls.

Emits one OpenInference-shaped OpenTelemetry span per LLM call carrying
*only* metadata — model, token counts, latency, error class, and the
request-scoped contextvars (request_id / user_id / tenant_id /
route_template). Prompt and response text are never recorded.

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
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from ..logging_config import (
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

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

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    def set_token_usage(
        self,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        """Record token counts on the span. Each is skipped when ``None``."""
        if prompt_tokens is not None:
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, prompt_tokens)
        if completion_tokens is not None:
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, completion_tokens)
        if total_tokens is not None:
            self._span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_TOTAL, total_tokens)

    def set_error_class(self, error_class: str) -> None:
        """Record a non-PHI error classifier (an exception/type name)."""
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


def _span_kind(operation: str) -> str:
    if operation == "embedding":
        return OpenInferenceSpanKindValues.EMBEDDING.value
    return OpenInferenceSpanKindValues.LLM.value


def _set_context_attributes(span: Span) -> None:
    """Merge the request-scoped contextvars onto the span (PHI-free)."""
    if (rid := request_id_var.get()) is not None:
        span.set_attribute(_ATTR_REQUEST_ID, rid)
    if (uid := user_id_var.get()) is not None:
        span.set_attribute(SpanAttributes.USER_ID, uid)
    if (tid := tenant_id_var.get()) is not None:
        span.set_attribute(_ATTR_TENANT_ID, tid)
    if (tmpl := route_template_var.get()) is not None:
        span.set_attribute(_ATTR_ROUTE_TEMPLATE, tmpl)


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
    with tracer.start_as_current_span(f"llm.{request.operation}") as span:
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
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        session = None
        if settings.llm_trace_use_id_token:
            session = _build_id_token_session(_endpoint_audience(endpoint))

        exporter = OTLPSpanExporter(endpoint=endpoint, session=session)
        resource = Resource.create({"service.name": settings.llm_trace_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _provider_installed.append(True)
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
    "LLMSpanRecorder",
    "LLMSpanRequest",
    "init_llm_tracing",
    "llm_span",
    "usage_tokens",
]
