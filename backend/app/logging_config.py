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
import re
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Final, override

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
    """

    user_id: str | None = None
    tenant_id: str | None = None


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
    }
)


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per record.

    Standard fields: timestamp, level, logger, message. Optional fields
    populated from contextvars: request_id, user_id, tenant_id,
    route_template. Any extra kwargs passed via `logger.info(..., extra=
    {...})` are merged at the top level (after PHI scrubbing).
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

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

        return json.dumps(payload, default=str, separators=(",", ":"))

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
