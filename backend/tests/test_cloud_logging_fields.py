# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Log lines carry the fields Cloud Logging and Error Reporting read.

Every test runs real records through the real ``JSONFormatter`` +
``RedactPHIFilter`` — through ``RequestContextMiddleware`` and the real
exception handlers where a request is involved, and through a separate
interpreter for the job crash — so what is asserted is the line a log
collector would receive.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.logging_config import (
    REPORTED_ERROR_EVENT_TYPE,
    JSONFormatter,
    RedactPHIFilter,
    bind_log_user_id,
    configure_job_logging,
    log_http_request_var,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)
from app.middleware.request_context import RequestContextMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

PROJECT = "demo-project"
TRACE_ID = "105445aa7843bc8bf206b12000100000"
TRACE_FIELDS = frozenset(
    {
        "logging.googleapis.com/trace",
        "logging.googleapis.com/spanId",
        "logging.googleapis.com/trace_sampled",
    }
)
_LOGGER = "cloud_logging_fields_test"
_BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _cloud_run_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setenv("K_SERVICE", "pablo-backend")
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)


@pytest.fixture(autouse=True)
def _clean_log_context() -> Iterator[None]:
    """Start each test with no request context, whatever an earlier test left set."""
    tokens = [
        (var, var.set(None))
        for var in (request_id_var, user_id_var, tenant_id_var, route_template_var)
    ]
    http_token = log_http_request_var.set(None)
    yield
    log_http_request_var.reset(http_token)
    for var, token in reversed(tokens):
        var.reset(token)


@contextmanager
def _json_logs(*names: str) -> Iterator[io.StringIO]:
    """Route the named loggers through a freshly built formatter (reads the env)."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())
    saved = []
    for name in names:
        lg = logging.getLogger(name)
        saved.append((lg, lg.handlers, lg.level, lg.propagate))
        lg.handlers = [handler]
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
    try:
        yield buf
    finally:
        for lg, handlers, level, propagate in saved:
            lg.handlers, lg.level, lg.propagate = handlers, level, propagate


def _records(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    lg = logging.getLogger(_LOGGER)

    @app.get("/patients/{patient_id}")
    async def read_patient(patient_id: str) -> dict[str, str]:
        bind_log_user_id("user-42")
        lg.error("patient_read_failed")
        return {"ok": "true"}

    @app.get("/patients/{patient_id}/boom")
    async def boom(patient_id: str) -> dict[str, str]:
        bind_log_user_id("user-42")
        raise RuntimeError("ledger out of balance")

    @app.get("/patients/{patient_id}/caught")
    async def caught(patient_id: str) -> dict[str, str]:
        bind_log_user_id("user-42")
        try:
            raise ValueError("bad row")
        except ValueError:
            lg.exception("row_rejected")
        return {"ok": "true"}

    return app


def _record(level: int, msg: str = "event") -> logging.LogRecord:
    return logging.LogRecord(_LOGGER, level, __file__, 1, msg, None, None)


class TestSeverity:
    @pytest.mark.parametrize(
        "level", [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL]
    )
    def test_severity_is_the_level_name_and_level_stays(self, level: int) -> None:
        payload = json.loads(JSONFormatter().format(_record(level)))
        assert payload["severity"] == logging.getLevelName(level)
        assert payload["level"] == logging.getLevelName(level)

    def test_an_unknown_level_is_default_not_an_invalid_severity(self) -> None:
        payload = json.loads(JSONFormatter().format(_record(25)))
        assert payload["severity"] == "DEFAULT"
        assert payload["level"] == "Level 25"

    def test_an_extra_cannot_override_severity(self) -> None:
        record = _record(logging.ERROR)
        record.severity = "INFO"
        assert json.loads(JSONFormatter().format(record))["severity"] == "ERROR"


class TestTraceCorrelation:
    def test_cloud_trace_header_reaches_route_and_access_lines(self) -> None:
        with _json_logs(_LOGGER, "pablo.access") as buf:
            TestClient(_build_app()).get(
                "/patients/p-1", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1234;o=1"}
            )

        route_line, access_line = _records(buf)
        assert route_line["severity"] == "ERROR"
        for line in (route_line, access_line):
            assert line["logging.googleapis.com/trace"] == f"projects/{PROJECT}/traces/{TRACE_ID}"
            # The header's span id is decimal; spanId is 16 hex characters.
            assert line["logging.googleapis.com/spanId"] == f"{1234:016x}"
            assert line["logging.googleapis.com/trace_sampled"] is True

    def test_traceparent_alone_is_enough(self) -> None:
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
        with _json_logs(_LOGGER) as buf:
            TestClient(_build_app()).get("/patients/p-1", headers={"traceparent": traceparent})

        (line,) = _records(buf)
        assert line["logging.googleapis.com/trace"] == (
            f"projects/{PROJECT}/traces/4bf92f3577b34da6a3ce929d0e0e4736"
        )
        assert line["logging.googleapis.com/spanId"] == "00f067aa0ba902b7"
        assert line["logging.googleapis.com/trace_sampled"] is False

    @pytest.mark.parametrize(
        "header",
        [
            "trace-abc/1;o=1",  # not a Cloud Trace id
            '"},"severity":"DEBUG/1',  # an attempt to smuggle a field in
            f"{'0' * 32}/1",  # the all-zero id is invalid
        ],
    )
    def test_a_malformed_trace_header_adds_no_trace(self, header: str) -> None:
        with _json_logs(_LOGGER) as buf:
            TestClient(_build_app()).get("/patients/p-1", headers={"X-Cloud-Trace-Context": header})

        (line,) = _records(buf)
        assert TRACE_FIELDS.isdisjoint(line)
        assert line["severity"] == "ERROR"

    def test_no_project_id_means_no_trace_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
        with _json_logs(_LOGGER) as buf:
            TestClient(_build_app()).get(
                "/patients/p-1", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1;o=1"}
            )

        (line,) = _records(buf)
        assert TRACE_FIELDS.isdisjoint(line)

    def test_the_trace_does_not_outlive_the_request(self) -> None:
        TestClient(_build_app()).get(
            "/patients/p-1", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1;o=1"}
        )
        assert log_http_request_var.get() is None


class TestErrorReportingEvent:
    def test_unhandled_exception_is_one_reported_error_event(self) -> None:
        with _json_logs("pablo.unhandled") as buf:
            response = TestClient(_build_app(), raise_server_exceptions=False).get(
                "/patients/p-1/boom", headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/7;o=1"}
            )

        assert response.status_code == 500
        (event,) = _records(buf)
        assert event["@type"] == REPORTED_ERROR_EVENT_TYPE
        assert event["severity"] == "ERROR"
        # The event name stays the message; the traceback is the stack_trace.
        assert event["message"] == "unhandled_exception"
        assert event["stack_trace"].startswith("Traceback (most recent call last):")
        assert event["stack_trace"].rstrip().endswith("RuntimeError: ledger out of balance")
        assert event["exc_info"] == event["stack_trace"]
        assert event["serviceContext"]["service"] == "pablo-backend"
        assert event["serviceContext"]["version"]
        assert event["context"] == {
            "httpRequest": {
                "method": "GET",
                "url": "/patients/{patient_id}/boom",
                "responseStatusCode": 500,
            },
            "user": "user-42",
        }
        # The 500 nests under its own request line too.
        assert event["logging.googleapis.com/trace"] == f"projects/{PROJECT}/traces/{TRACE_ID}"

    def test_caught_exception_logged_in_a_request_is_reported(self) -> None:
        with _json_logs(_LOGGER) as buf:
            TestClient(_build_app()).get("/patients/p-1/caught")

        (event,) = _records(buf)
        assert event["@type"] == REPORTED_ERROR_EVENT_TYPE
        assert event["message"] == "row_rejected"
        assert "ValueError: bad row" in event["stack_trace"]
        # Mid-request the status is not known yet, so none is claimed.
        assert event["context"] == {
            "httpRequest": {"method": "GET", "url": "/patients/{patient_id}/caught"},
            "user": "user-42",
        }

    def test_exception_outside_a_request_has_no_request_context(self) -> None:
        try:
            raise OSError("disk full")
        except OSError:
            record = logging.LogRecord(
                _LOGGER, logging.ERROR, __file__, 1, "write_failed", None, sys.exc_info()
            )
        event = json.loads(JSONFormatter().format(record))
        assert event["@type"] == REPORTED_ERROR_EVENT_TYPE
        assert "context" not in event
        assert TRACE_FIELDS.isdisjoint(event)

    def test_a_warning_with_an_exception_is_not_an_error_event(self) -> None:
        try:
            raise TimeoutError("slow upstream")
        except TimeoutError:
            record = logging.LogRecord(
                _LOGGER, logging.WARNING, __file__, 1, "retrying", None, sys.exc_info()
            )
        payload = json.loads(JSONFormatter().format(record))
        assert payload["severity"] == "WARNING"
        assert "TimeoutError" in payload["exc_info"]
        assert "@type" not in payload
        assert "stack_trace" not in payload

    def test_stack_trace_is_scrubbed_like_the_message(self) -> None:
        try:
            raise ValueError("could not parse 123-45-6789")
        except ValueError:
            record = logging.LogRecord(
                _LOGGER, logging.ERROR, __file__, 1, "parse_failed", None, sys.exc_info()
            )
        event = json.loads(JSONFormatter().format(record))
        assert "123-45-6789" not in event["stack_trace"]
        assert "[REDACTED-SSN]" in event["stack_trace"]


class TestNothingIdentifyingInTheContext:
    """The concrete URL — record id, query string — never reaches a log line."""

    PATIENT_ID = "8f14e45f-ceea-467a-9575-6b3a1f1e2c11"
    QUERY = "?email=jane.doe@example.com&name=Jane+Doe&dob=1980-01-01"

    @pytest.mark.parametrize("suffix", ["", "/boom", "/caught"])
    def test_no_path_id_query_string_or_email(self, suffix: str) -> None:
        with _json_logs(_LOGGER, "pablo.unhandled", "pablo.access") as buf:
            TestClient(_build_app(), raise_server_exceptions=False).get(
                f"/patients/{self.PATIENT_ID}{suffix}{self.QUERY}",
                headers={"X-Cloud-Trace-Context": f"{TRACE_ID}/1;o=1"},
            )

        raw = buf.getvalue()
        assert raw.strip()
        for needle in (self.PATIENT_ID, "jane", "Jane", "example.com", "1980-01-01", "?"):
            assert needle not in raw
        reported = [r for r in _records(buf) if r.get("@type") == REPORTED_ERROR_EVENT_TYPE]
        assert len(reported) == (0 if suffix == "" else 1)
        for event in reported:
            assert event["context"]["httpRequest"]["url"].startswith("/patients/{patient_id}")
            assert event["context"]["user"] == "user-42"


class TestJobCrash:
    def test_an_uncaught_job_exception_is_one_structured_entry(self) -> None:
        script = textwrap.dedent(
            """
            import logging
            from app.logging_config import configure_job_logging

            configure_job_logging()
            logging.getLogger("job").info("job_started")
            raise RuntimeError("stage failed")
            """
        )
        env = {
            **os.environ,
            "PYTHONPATH": str(_BACKEND_DIR),
            "CLOUD_RUN_JOB": "pablo-claims-pipeline",
            "PABLO_VERSION": "9.9.9",
        }
        env.pop("K_SERVICE", None)
        # S603: the argv is this interpreter plus a literal script, no shell. A
        # separate process is the point: only a fresh interpreter has no logging
        # configured and Python's own excepthook in place, as a job does.
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=_BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 1
        assert result.stderr == ""
        started, crash = (json.loads(line) for line in result.stdout.splitlines())
        assert started["severity"] == "INFO"
        assert crash["severity"] == "CRITICAL"
        assert crash["message"] == "uncaught_exception"
        assert crash["@type"] == REPORTED_ERROR_EVENT_TYPE
        assert crash["serviceContext"] == {"service": "pablo-claims-pipeline", "version": "9.9.9"}
        assert crash["stack_trace"].rstrip().endswith("RuntimeError: stage failed")
        assert "context" not in crash

    def test_existing_logging_setup_is_left_alone(self) -> None:
        root = logging.getLogger()
        sentinel = logging.NullHandler()
        saved_handlers, saved_hook = root.handlers[:], sys.excepthook
        root.handlers = [sentinel]
        try:
            configure_job_logging()
            assert root.handlers == [sentinel]
        finally:
            root.handlers = saved_handlers
            sys.excepthook = saved_hook
