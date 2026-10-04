# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Structured-output gateway backed by Amazon Bedrock.

A provider on a second cloud for one-shot structured calls, so a call the
primary model fails or stalls on can be answered by a model with
independent capacity. Uses the Bedrock Converse API, which speaks one
request shape for every model family it serves. As on the Anthropic path,
structure is enforced with a single forced tool call whose input schema
*is* the caller's ``response_schema``, so the answer is validated against
the same schema the Gemini path uses.

Selected for ``bedrock:``-prefixed model ids (for example
``bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0``) by
:func:`~app.services.structured_llm_gateway.resolve_structured_llm_gateway`,
so it can be named as a fallback in ``AI_MODEL_FLASH_FALLBACKS``.

Bounds. Each attempt is bounded by the botocore read timeout, and
botocore's own retries are off: attempts belong to the caller's retry
policy (``SINGLE_ATTEMPT`` under a hedged run, where a retry is the next
leg). A Converse response arrives whole, so the read timeout bounds the
wait for the answer itself.

Failures. Each attempt's failure is translated into something the shared
classifier (:mod:`app.reliability.classify`) reads: a timeout is a
``TimeoutError``, an unreachable endpoint a ``ConnectionError``, and a
service error a :class:`BedrockCallError` carrying its HTTP status, so a
429 or 5xx is transient and other 4xx are not. Under the interactive
hedge a transient failure hands over to the next leg; anything else (no
access to the model, bad credentials, a request Bedrock rejects) is
permanent for this model, so it is not asked again but the other models
still are.

Credentials. With ``aws_bedrock_role_arn`` set, the gateway assumes that
role through web identity federation, presenting a Google-signed ID token
for the workload's own service account, so no AWS keys are stored
anywhere. Unset, botocore's standard credential chain applies
(environment, ``AWS_PROFILE``, ``AWS_WEB_IDENTITY_TOKEN_FILE``, an
instance or task role).

Logging. Only the exception class, the service's error code, the HTTP
status and the model id are logged. Prompts, answers and service error
messages (which can quote the request) never are.
"""

from __future__ import annotations

import functools
import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any

from ..reliability import LLM_REQUEST, Idempotency, RetryExhaustedError, call_with_retry
from .llm_provider import LLMProvider, strip_provider_prefix
from .llm_telemetry import LLMSpanRequest, llm_span
from .structured_llm_gateway import (
    _STRUCTURED_LLM_TIMEOUT_SECONDS,
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    _attempt_timeout,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..reliability import RetryPolicy

logger = logging.getLogger(__name__)

#: Connecting should take well under a second; a short attempt bound is not
#: spent waiting on a TCP handshake.
_MAX_CONNECT_TIMEOUT_SECONDS = 5.0

#: Bounds are rounded up to this step before a client is built for them, so
#: a caller passing what is left of a budget reuses a handful of clients
#: rather than building one per call.
_TIMEOUT_STEP_SECONDS = 0.5

_TOOL_NAME = "emit_structured_output"

#: Bedrock's own error codes for a model that did not answer in time.
_TIMEOUT_CODES = frozenset({"ModelTimeoutException"})


class BedrockCallError(RuntimeError):
    """Bedrock answered with an error.

    ``status_code`` is the HTTP status of the response, read by the shared
    classifier: 429 and 5xx are worth another attempt, other 4xx are not.
    ``error_code`` is the service's own name for it (``ThrottlingException``,
    ``AccessDeniedException``, ...).
    """

    def __init__(self, message: str, *, status_code: int | None, error_code: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def to_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Translate the gateway's schema dialect into standard JSON Schema.

    Callers write schemas in the dialect Gemini accepts, where a nullable
    field is ``{"type": "string", "nullable": true}``. JSON Schema has no
    ``nullable``; the same field is ``{"type": ["string", "null"]}``, and a
    nullable enum must list ``null`` among its values or a null answer fails
    validation.
    """
    out: dict[str, Any] = {k: v for k, v in schema.items() if k != "nullable"}
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: to_json_schema(v) for k, v in out["properties"].items()}
    if isinstance(out.get("items"), dict):
        out["items"] = to_json_schema(out["items"])
    if schema.get("nullable"):
        json_type = out.get("type")
        if isinstance(json_type, str):
            out["type"] = [json_type, "null"]
        if "enum" in out and None not in out["enum"]:
            out["enum"] = [*out["enum"], None]
    return out


def google_id_token(audience: str) -> str:
    """A Google-signed ID token for the workload's own service account.

    On Cloud Run or GCE this comes from the metadata server; elsewhere from
    a service-account key named by ``GOOGLE_APPLICATION_CREDENTIALS``.
    """
    import google.auth.transport.requests
    import google.oauth2.id_token

    token: str = google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), audience
    )
    return token


def build_botocore_session(
    *,
    region: str,
    role_arn: str | None,
    audience: str,
    token_loader: Callable[[str], str] = google_id_token,
) -> Any:
    """A botocore session whose credentials come from the configured source.

    With ``role_arn`` set, credentials are fetched by
    ``AssumeRoleWithWebIdentity`` on first use and refreshed before they
    expire; every client built from the session shares them.
    """
    import botocore.session
    from botocore.credentials import (
        AssumeRoleWithWebIdentityCredentialFetcher,
        CredentialProvider,
        DeferredRefreshableCredentials,
    )

    session = botocore.session.Session()
    if role_arn is None:
        return session

    fetcher = AssumeRoleWithWebIdentityCredentialFetcher(
        # A regional STS endpoint, in the region the calls go to.
        client_creator=functools.partial(session.create_client, region_name=region),
        web_identity_token_loader=lambda: token_loader(audience),
        role_arn=role_arn,
        extra_args={"RoleSessionName": "pablo-structured-llm"},
    )

    class _GoogleWebIdentityProvider(CredentialProvider):  # type: ignore[misc]
        METHOD = "google-web-identity"

        def load(self) -> Any:
            return DeferredRefreshableCredentials(
                refresh_using=fetcher.fetch_credentials, method=self.METHOD
            )

    session.get_component("credential_provider").insert_before("env", _GoogleWebIdentityProvider())
    return session


class BedrockStructuredLLMGateway(StructuredLLMGateway):
    """One-shot structured completion through Bedrock Converse."""

    def __init__(
        self,
        *,
        region: str | None = None,
        role_arn: str | None = None,
        audience: str | None = None,
        client_factory: Callable[[float], Any] | None = None,
    ) -> None:
        self._region = region
        self._role_arn = role_arn
        self._audience = audience
        # Test seam: (timeout_seconds) -> a bedrock-runtime client.
        self._client_factory = client_factory
        self._session: Any = None
        # One client per bound: botocore fixes timeouts when a client is
        # built, and building one costs tens of milliseconds. Hedged legs
        # run on worker threads, hence the lock.
        self._clients: dict[float, Any] = {}
        self._lock = threading.Lock()

    def _client(self, timeout_seconds: float) -> Any:
        bound = math.ceil(timeout_seconds / _TIMEOUT_STEP_SECONDS) * _TIMEOUT_STEP_SECONDS
        with self._lock:
            if bound not in self._clients:
                factory = self._client_factory or self._build_client
                self._clients[bound] = factory(bound)
            return self._clients[bound]

    def _build_client(self, timeout_seconds: float) -> Any:
        from botocore.config import Config

        from ..settings import get_settings

        settings = get_settings()
        region = self._region or settings.aws_bedrock_region
        if self._session is None:
            self._session = build_botocore_session(
                region=region,
                role_arn=self._role_arn or settings.aws_bedrock_role_arn,
                audience=self._audience or settings.aws_bedrock_web_identity_audience,
            )
        return self._session.create_client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                read_timeout=timeout_seconds,
                connect_timeout=min(timeout_seconds, _MAX_CONNECT_TIMEOUT_SECONDS),
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
        timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> StructuredCompletion:
        # Never hold a pooled DB connection across the model round-trip — the
        # caller must release_db_connection() first (raises in dev/test).
        from ..db import assert_no_held_db_connection

        assert_no_held_db_connection("structured-llm")

        # ``thinking_budget`` is part of the shared contract but unused here:
        # the forced tool call is a single non-thinking completion.
        del thinking_budget

        bound = timeout_seconds if timeout_seconds is not None else _STRUCTURED_LLM_TIMEOUT_SECONDS
        policy = retry_policy or LLM_REQUEST
        normalized_model = strip_provider_prefix(model)
        request: dict[str, Any] = {
            "modelId": normalized_model,
            "system": [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_prompt}]}],
            "inferenceConfig": {"maxTokens": max_output_tokens, "temperature": temperature},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": _TOOL_NAME,
                            "description": "Return the result as structured output.",
                            "inputSchema": {"json": to_json_schema(response_schema)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": _TOOL_NAME}},
            },
        }
        started = time.monotonic()

        def attempt() -> dict[str, Any]:
            attempt_bound = _attempt_timeout(bound, started, policy)
            try:
                response: dict[str, Any] = self._client(attempt_bound).converse(**request)
            except Exception as exc:
                raise _translate_error(exc, attempt_bound) from exc
            return response

        with llm_span(
            LLMSpanRequest(
                operation="structured", model=normalized_model, provider=LLMProvider.BEDROCK
            )
        ) as span:
            try:
                response = call_with_retry(attempt, policy=policy, idempotency=Idempotency.SAFE)
            except RetryExhaustedError as exc:
                _log_failure(exc.last_exc, normalized_model)
                raise RuntimeError(f"Structured LLM call failed: {exc.last_exc}") from exc
            except Exception as exc:
                _log_failure(exc, normalized_model)
                raise RuntimeError(
                    f"Structured LLM call failed: Bedrock could not serve it ({_describe(exc)})"
                ) from exc
            usage = response.get("usage") or {}
            output_tokens = usage.get("outputTokens")
            span.set_token_usage(
                prompt_tokens=usage.get("inputTokens"),
                completion_tokens=output_tokens,
                total_tokens=usage.get("totalTokens"),
            )

        # A token-capped answer is a partial tool call (or none at all); say
        # so before parsing, so it is not mistaken for a malformed one.
        stop_reason = response.get("stopReason")
        if stop_reason == "max_tokens":
            raise StructuredOutputTruncatedError(
                f"LLM output truncated at max_output_tokens={max_output_tokens} "
                f"(model={normalized_model}). Retry with a larger output budget."
            )
        finish_reason = (
            "safety" if stop_reason in {"guardrail_intervened", "content_filtered"} else "stop"
        )

        content = ((response.get("output") or {}).get("message") or {}).get("content") or []
        tool_input = next(
            (
                block["toolUse"].get("input")
                for block in content
                if isinstance(block, dict)
                and isinstance(block.get("toolUse"), dict)
                and block["toolUse"].get("name") == _TOOL_NAME
            ),
            None,
        )
        if tool_input is None:
            raise ValueError(
                f"Bedrock response had no '{_TOOL_NAME}' tool call (stop_reason={stop_reason})"
            )
        if not isinstance(tool_input, dict):
            raise ValueError(
                f"Structured tool input was not an object ({type(tool_input).__name__})"
            )

        return StructuredCompletion(
            data=tool_input,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )


def _describe(exc: BaseException) -> str:
    """A content-free description: class, service code and HTTP status."""
    if isinstance(exc, BedrockCallError):
        return f"{exc.error_code or type(exc).__name__}, HTTP {exc.status_code}"
    return type(exc).__name__


def _log_failure(exc: BaseException, model: str) -> None:
    logger.warning(
        "Bedrock structured completion failed: %s (model=%s)",
        _describe(exc),
        model,
    )


def _translate_error(exc: Exception, bound: float) -> Exception:
    """Map a botocore failure onto an exception the shared classifier reads.

    The messages are built here rather than carried over: a service error
    message can quote the request.
    """
    from botocore.exceptions import (
        ClientError,
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        ReadTimeoutError,
    )

    if isinstance(exc, (ReadTimeoutError, ConnectTimeoutError)):
        return TimeoutError(f"Bedrock gave no answer within {bound:g} s")
    if isinstance(exc, (EndpointConnectionError, ConnectionClosedError)):
        return ConnectionError(f"Bedrock connection failed ({type(exc).__name__})")
    if isinstance(exc, ClientError):
        error = exc.response.get("Error") or {}
        code = error.get("Code")
        if code in _TIMEOUT_CODES:
            return TimeoutError(f"Bedrock model timed out ({code})")
        status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        status = status if isinstance(status, int) else None
        return BedrockCallError(
            f"Bedrock returned {code or 'an error'} (HTTP {status})",
            status_code=status,
            error_code=code,
        )
    return exc


__all__ = [
    "BedrockCallError",
    "BedrockStructuredLLMGateway",
    "build_botocore_session",
    "google_id_token",
    "to_json_schema",
]
