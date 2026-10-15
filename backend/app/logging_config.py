# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Structured JSON logging with PHI scrubbing.

This module installs a root-logger configuration that:

1. Emits one JSON object per log record (single line, suitable for Cloud
   Logging ingestion).
2. Runs every record through a PHI scrubber filter that denies a known
   set of PHI-named keys and regex-scrubs the rendered message for a
   small set of obvious PHI shapes (SSN, email, US phone).
3. Reads request-scoped context (request_id, user_id, tenant_id,
   route_template, ...) from contextvars defined here. The middleware
   that populates these vars lives in `app.middleware.request_context`
   (THERAPY-2pf4); the auth dependency chain and the database session
   middleware populate user_id / tenant_id through ``bind_log_user_id`` /
   ``bind_log_tenant_id`` once the caller is known.
4. Speaks Cloud Logging's structured-log dialect: ``severity`` (so a line
   is filed at its real level, not DEFAULT), the ``logging.googleapis.com/
   trace`` / ``spanId`` pair (so app lines nest under the request line of
   the same request), and, for an ERROR logged with an exception, an Error
   Reporting ``ReportedErrorEvent`` (so the exception is grouped, counted
   and attributed to a user id). ``level`` stays alongside ``severity``
   for anything already filtering on it.

Design contract: log messages MUST NOT contain PHI. The deny-list is the
load-bearing defense — the regex layer catches accidents, but free-text
PHI ("patient John Smith said ...") is out of scope for this filter. A
CI guardrail blocks new `logger.*(...)` callsites that pass PHI-keyed
kwargs.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, override

from . import __version__

if TYPE_CHECKING:
    from types import TracebackType

PHI_DENY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "patient_id",
        "patient_name",
        "patient_email",
        "patient_phone",
        "patient_dob",
        "dob",
        "ssn",
        "soap_text",
        "transcript",
        "audio_path",
        "note_content",
        "prompt_text",
        "chat_message_content",
        "chat_content",
        "message_content",
    }
)

_REDACTED: Final[str] = "[REDACTED]"

_SSN_RE: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE: Final = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE: Final = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")

_SCRUBBERS: Final = (
    (_SSN_RE, "[REDACTED-SSN]"),
    (_EMAIL_RE, "[REDACTED-EMAIL]"),
    (_PHONE_RE, "[REDACTED-PHONE]"),
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
route_template_var: ContextVar[str | None] = ContextVar("route_template", default=None)


@dataclass
class RequestLogIdentity:
    """Who a request is acting for, as far as the logs are concerned.

    Mutable on purpose. Sync FastAPI dependencies run in a worker thread on a
    COPY of the request's context, so a ``user_id_var.set(...)`` made there
    dies with the thread and every later log line for the request — the
    async route body, the error handlers, the access line — goes out without
    it. The copy still points at the same object, though, so a field written
    here from the worker is visible everywhere else in the request.

    One instance per request, created by ``RequestContextMiddleware`` and
    dropped when the request ends, so nothing carries over to the next one.

    It also carries how the request ended, for the access line: the error
    code of a failed request, the field paths a validation failure named,
    the reason a credential was refused, and — for an expired token only —
    the user the token claimed to be. Every value is an opaque id or a fixed
    code; none is a message, a submitted value or a resolved path.
    """

    user_id: str | None = None
    tenant_id: str | None = None
    # Who an EXPIRED token said it was. Never verified and never used to
    # decide anything; it lets a burst of 401s before a re-sign-in be read
    # as one person's session instead of an anonymous one.
    claimed_user_id: str | None = None
    auth_failure: str | None = None
    error_code: str | None = None
    error_fields: tuple[str, ...] | None = None
    error_type: str | None = None

    def outcome_fields(self) -> dict[str, object]:
        """The outcome fields that were set, keyed as the access line names them."""
        fields: dict[str, object] = {
            "claimed_user_id": self.claimed_user_id,
            "auth_failure": self.auth_failure,
            "error_code": self.error_code,
            "error_fields": list(self.error_fields) if self.error_fields else None,
            "error_type": self.error_type,
        }
        return {key: value for key, value in fields.items() if value is not None}


_request_log_identity_var: ContextVar[RequestLogIdentity | None] = ContextVar(
    "request_log_identity", default=None
)


def begin_request_log_identity() -> tuple[RequestLogIdentity, Token[RequestLogIdentity | None]]:
    """Install a fresh identity holder for one request; reset with the token."""
    identity = RequestLogIdentity()
    return identity, _request_log_identity_var.set(identity)


def end_request_log_identity(token: Token[RequestLogIdentity | None]) -> None:
    _request_log_identity_var.reset(token)


def bind_log_user_id(user_id: str) -> None:
    """Attribute every remaining log line of this request to ``user_id``."""
    user_id_var.set(user_id)
    if (identity := _request_log_identity_var.get()) is not None:
        identity.user_id = user_id


def bind_log_tenant_id(tenant_id: str) -> None:
    """Attribute every remaining log line of this request to ``tenant_id``."""
    tenant_id_var.set(tenant_id)
    if (identity := _request_log_identity_var.get()) is not None:
        identity.tenant_id = tenant_id


def bind_log_claimed_user_id(user_id: str) -> None:
    """Note who an expired token claimed to be, for this request's access line."""
    if (identity := _request_log_identity_var.get()) is not None:
        identity.claimed_user_id = user_id


def record_log_outcome(
    *,
    error_code: str | None = None,
    error_fields: tuple[str, ...] | None = None,
    error_type: str | None = None,
    auth_failure: str | None = None,
) -> None:
    """Record how this request failed. The first value recorded for a field wins.

    First-wins because the innermost handler knows the most: a validation
    handler names the fields, and the middleware's catch-all that runs after
    it must not overwrite that with a generic code.
    """
    identity = _request_log_identity_var.get()
    if identity is None:
        return
    if error_code is not None and identity.error_code is None:
        identity.error_code = error_code
    if error_fields and identity.error_fields is None:
        identity.error_fields = error_fields
    if error_type is not None and identity.error_type is None:
        identity.error_type = error_type
    if auth_failure is not None and identity.auth_failure is None:
        identity.auth_failure = auth_failure


def current_log_user_id() -> str | None:
    if (uid := user_id_var.get()) is not None:
        return uid
    identity = _request_log_identity_var.get()
    return identity.user_id if identity is not None else None


def current_log_tenant_id() -> str | None:
    if (tid := tenant_id_var.get()) is not None:
        return tid
    identity = _request_log_identity_var.get()
    return identity.tenant_id if identity is not None else None


@dataclass(frozen=True)
class LogTrace:
    """The upstream trace a request belongs to, as Cloud Logging spells it.

    ``trace_id`` is 32 lowercase hex characters and ``span_id`` 16, the
    encodings ``LogEntry.trace`` / ``LogEntry.spanId`` expect. Both are
    validated at parse time (``app.middleware.request_context``), so a
    caller-supplied header can never put an arbitrary string here.
    """

    trace_id: str
    span_id: str | None = None
    sampled: bool | None = None


@dataclass(frozen=True)
class LogHttpRequest:
    """What an error report may say about the request it happened in.

    The method and trace only — the URL an error report shows is the matched
    route template (from ``route_template``), never the concrete path or the
    query string, which carry record ids and search terms.
    ``response_status_code`` is set by whoever knows it (the unhandled-
    exception handler: 500); a line logged mid-request leaves it unset.
    """

    method: str
    trace: LogTrace | None = None
    response_status_code: int | None = None


log_http_request_var: ContextVar[LogHttpRequest | None] = ContextVar(
    "log_http_request", default=None
)

# An ``extra`` key that overrides ``log_http_request_var`` for one record. The
# unhandled-exception handler needs it: it runs after the request-context
# middleware has reset the var. Never copied into the payload itself.
LOG_HTTP_REQUEST_EXTRA: Final[str] = "log_http_request"

# LogSeverity names. Python's level names are a subset; a custom level that is
# not one of these goes out as DEFAULT rather than as a value Cloud Logging
# would not recognise.
_CLOUD_SEVERITIES: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"}
)

_TRACE_KEY: Final[str] = "logging.googleapis.com/trace"
_SPAN_ID_KEY: Final[str] = "logging.googleapis.com/spanId"
_TRACE_SAMPLED_KEY: Final[str] = "logging.googleapis.com/trace_sampled"
REPORTED_ERROR_EVENT_TYPE: Final[str] = (
    "type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent"
)
_DEFAULT_SERVICE_NAME: Final[str] = "pablo"


def _scrub_text(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


class RedactPHIFilter(logging.Filter):
    """Strip PHI from every log record before the formatter runs.

    Layer 1: any extra field whose key is in PHI_DENY_KEYS is replaced
    with "[REDACTED]" — this is the load-bearing defense.

    Layer 2: the rendered message string is run through a small set of
    PHI-shape regexes. This is belt-and-suspenders; the real contract
    is that callers do not put PHI in messages.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key in list(record.__dict__):
            if key in PHI_DENY_KEYS:
                record.__dict__[key] = _REDACTED

        if isinstance(record.args, dict):
            record.args = {
                k: (_REDACTED if k in PHI_DENY_KEYS else v) for k, v in record.args.items()
            }

        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            rendered = str(record.msg)
        scrubbed = _scrub_text(rendered)
        if scrubbed != rendered:
            record.msg = scrubbed
            record.args = None
        return True


_STANDARD_LOGRECORD_ATTRS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
        LOG_HTTP_REQUEST_EXTRA,
    }
)


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per record.

    Standard fields: timestamp, severity, level, logger, message. Optional
    fields populated from contextvars: request_id, user_id, tenant_id,
    route_template, and the Cloud Logging trace / spanId of the request.
    Any extra kwargs passed via `logger.info(..., extra={...})` are merged
    at the top level (after PHI scrubbing).

    The trace fields need the project id (``GOOGLE_CLOUD_PROJECT``, else
    ``GCP_PROJECT_ID``) and are left out without one. The Error Reporting
    service name comes from the Cloud Run environment (``K_SERVICE`` for a
    service, ``CLOUD_RUN_JOB`` for a job) and the version is the release
    (``PABLO_VERSION``, else the packaged ``VERSION``). All of it is read
    once, when the formatter is built.
    """

    def __init__(self) -> None:
        super().__init__()
        env = os.environ
        self._project_id = env.get("GOOGLE_CLOUD_PROJECT") or env.get("GCP_PROJECT_ID") or None
        self._service_context: dict[str, str] = {
            "service": env.get("K_SERVICE") or env.get("CLOUD_RUN_JOB") or _DEFAULT_SERVICE_NAME
        }
        if version := env.get("PABLO_VERSION") or __version__:
            self._service_context["version"] = version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "severity": (record.levelname if record.levelname in _CLOUD_SEVERITIES else "DEFAULT"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        http_request = getattr(record, LOG_HTTP_REQUEST_EXTRA, None)
        if not isinstance(http_request, LogHttpRequest):
            http_request = log_http_request_var.get()
        if http_request is not None and http_request.trace is not None:
            self._add_trace(payload, http_request.trace)

        if (rid := request_id_var.get()) is not None:
            payload["request_id"] = rid
        if (uid := current_log_user_id()) is not None:
            payload["user_id"] = uid
        if (tid := current_log_tenant_id()) is not None:
            payload["tenant_id"] = tid
        if (tmpl := route_template_var.get()) is not None:
            payload["route_template"] = tmpl

        for key, value in record.__dict__.items():
            if key in _STANDARD_LOGRECORD_ATTRS or key in payload:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["error_class"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            # The traceback carries the exception's own message, which can echo
            # PHI from a wrapped ValueError (e.g. a note/transcript parse error).
            # RedactPHIFilter only scrubs the log message, not exc_info, so run
            # the same Layer-2 shape-scrub here — parity with the message path.
            payload["exc_info"] = _scrub_text(self.formatException(record.exc_info))
            if record.levelno >= logging.ERROR and record.exc_info[0] is not None:
                self._add_error_report(payload, http_request)

        return json.dumps(payload, default=str, separators=(",", ":"))

    def _add_trace(self, payload: dict[str, Any], trace: LogTrace) -> None:
        if self._project_id is None:
            return
        payload[_TRACE_KEY] = f"projects/{self._project_id}/traces/{trace.trace_id}"
        if trace.span_id is not None:
            payload[_SPAN_ID_KEY] = trace.span_id
        if trace.sampled is not None:
            payload[_TRACE_SAMPLED_KEY] = trace.sampled

    def _add_error_report(
        self, payload: dict[str, Any], http_request: LogHttpRequest | None
    ) -> None:
        """Shape an exception record as an Error Reporting ``ReportedErrorEvent``.

        The traceback goes in ``stack_trace``, which Error Reporting reads
        before ``message`` — so ``message`` keeps the event name callers and
        alerts match on, and the event is still grouped by its stack. It is
        the already-scrubbed ``exc_info`` text; ``exc_info`` stays for
        readers that predate this field.

        ``context`` carries ids only: the user id (opaque, never an email)
        and, inside a request, its method, matched route template and status.
        """
        payload["@type"] = REPORTED_ERROR_EVENT_TYPE
        payload["serviceContext"] = dict(self._service_context)
        payload["stack_trace"] = payload["exc_info"]

        context: dict[str, Any] = {}
        if http_request is not None:
            request_context: dict[str, Any] = {"method": http_request.method}
            if isinstance(route := payload.get("route_template"), str):
                request_context["url"] = route
            if http_request.response_status_code is not None:
                request_context["responseStatusCode"] = http_request.response_status_code
            context["httpRequest"] = request_context
        if isinstance(user_id := payload.get("user_id"), str):
            context["user"] = user_id
        if context:
            payload["context"] = context

    @override
    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        # Stable ISO-8601 UTC with millisecond precision.
        ts = _dt.datetime.fromtimestamp(record.created, tz=_dt.UTC)
        return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


def configure_logging(level: str = "INFO") -> None:
    """Install JSON formatter + PHI filter on the root logger.

    Idempotent: replaces existing handlers rather than appending, so
    re-calling at import / test setup time does not stack duplicates.
    """
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())
    root.addHandler(handler)

    # uvicorn ships its own handlers — strip them so its records flow
    # through our root config and get JSON-formatted + scrubbed too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


_uncaught_logger = logging.getLogger("pablo.uncaught")


def _log_uncaught_exception(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    """``sys.excepthook`` for jobs: the crash is one structured entry.

    Python's default hook prints the traceback to stderr a line at a time,
    which a log collector files as one entry per frame, none of them
    structured. Ctrl-C keeps the default behaviour.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _uncaught_logger.critical("uncaught_exception", exc_info=(exc_type, exc, tb))


def configure_job_logging(level: str = "INFO") -> None:
    """Logging for a command-line entrypoint (a scheduled job).

    A drop-in for the ``logging.basicConfig(...)`` a job used to call, with
    the same rule: when the process has already configured logging (a
    wrapper that calls this job's ``run``, a test harness), its setup is
    left alone. Otherwise the job logs through the same JSON formatter and
    PHI filter as the web service.

    Either way an exception that escapes the job is logged once, through
    whatever handler is installed, instead of as a raw stderr traceback.
    """
    if not logging.getLogger().handlers:
        configure_logging(level=level)
    sys.excepthook = _log_uncaught_exception
