# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for structured JSON logging + PHI scrubber (THERAPY-za2y)."""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Generator, Mapping
from typing import Any

import pytest
from app.logging_config import (
    JSONFormatter,
    RedactPHIFilter,
    configure_logging,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)


def _make_record(
    msg: str,
    *,
    args: tuple[object, ...] | Mapping[str, object] | None = None,
    extra: Mapping[str, Any] | None = None,
    level: int = logging.INFO,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            record.__dict__[k] = v
    return record


class TestRedactPHIFilter:
    def setup_method(self) -> None:
        self.f = RedactPHIFilter()

    def test_denylist_key_in_extra(self) -> None:
        rec = _make_record("op happened", extra={"patient_id": "abc-123"})
        self.f.filter(rec)
        assert rec.__dict__["patient_id"] == "[REDACTED]"

    def test_denylist_covers_all_phi_keys(self) -> None:
        keys = {
            "patient_id": "p1",
            "patient_name": "John Smith",
            "soap_text": "S: pt presents...",
            "transcript": "hello world",
            "audio_path": "gs://...",
            "note_content": "...",
            "prompt_text": "...",
            "chat_message_content": "...",
        }
        rec = _make_record("note saved", extra=keys)
        self.f.filter(rec)
        for k in keys:
            assert rec.__dict__[k] == "[REDACTED]", k

    def test_denylist_in_dict_args(self) -> None:
        # Python logging accepts a dict as the sole positional arg for
        # %(key)s-style formatting. LogRecord unwraps the tuple to a dict
        # when len(args) == 1 and args[0] is a Mapping.
        rec = _make_record("%(patient_id)s saved", args=({"patient_id": "abc-123"},))
        self.f.filter(rec)
        assert isinstance(rec.args, dict)
        assert rec.args["patient_id"] == "[REDACTED]"

    def test_message_ssn_scrubbed(self) -> None:
        rec = _make_record("user submitted 123-45-6789 as identifier")
        self.f.filter(rec)
        assert "123-45-6789" not in rec.getMessage()
        assert "[REDACTED-SSN]" in rec.getMessage()

    def test_message_email_scrubbed(self) -> None:
        rec = _make_record("contact at jane.doe@example.com please")
        self.f.filter(rec)
        assert "jane.doe@example.com" not in rec.getMessage()
        assert "[REDACTED-EMAIL]" in rec.getMessage()

    def test_message_phone_scrubbed(self) -> None:
        rec = _make_record("call 415-555-1234 today")
        self.f.filter(rec)
        assert "415-555-1234" not in rec.getMessage()
        assert "[REDACTED-PHONE]" in rec.getMessage()

    def test_non_phi_message_passes_through(self) -> None:
        rec = _make_record("user 7f3c logged in")
        self.f.filter(rec)
        assert rec.getMessage() == "user 7f3c logged in"

    def test_unrelated_extra_fields_preserved(self) -> None:
        rec = _make_record("op", extra={"latency_ms": 42, "route_template": "/api/x"})
        self.f.filter(rec)
        assert rec.__dict__["latency_ms"] == 42
        assert rec.__dict__["route_template"] == "/api/x"


class TestJSONFormatter:
    def setup_method(self) -> None:
        self.fmt = JSONFormatter()

    def test_single_line_json(self) -> None:
        rec = _make_record("hello")
        out = self.fmt.format(rec)
        assert "\n" not in out
        payload = json.loads(out)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test"
        assert "timestamp" in payload

    def test_contextvars_included_when_set(self) -> None:
        request_id_var.set("req-abc")
        user_id_var.set("user-1")
        tenant_id_var.set("tenant-1")
        route_template_var.set("/api/patients/{id}")
        try:
            payload = json.loads(self.fmt.format(_make_record("op")))
            assert payload["request_id"] == "req-abc"
            assert payload["user_id"] == "user-1"
            assert payload["tenant_id"] == "tenant-1"
            assert payload["route_template"] == "/api/patients/{id}"
        finally:
            request_id_var.set(None)
            user_id_var.set(None)
            tenant_id_var.set(None)
            route_template_var.set(None)

    def test_contextvars_omitted_when_unset(self) -> None:
        payload = json.loads(self.fmt.format(_make_record("op")))
        assert "request_id" not in payload
        assert "user_id" not in payload

    def test_extra_fields_merged(self) -> None:
        rec = _make_record("op", extra={"latency_ms": 12, "status_code": 200})
        payload = json.loads(self.fmt.format(rec))
        assert payload["latency_ms"] == 12
        assert payload["status_code"] == 200

    def test_exception_info_serialized(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            rec = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="failed",
                args=None,
                exc_info=sys.exc_info(),
            )
        payload = json.loads(self.fmt.format(rec))
        assert payload["error_class"] == "ValueError"
        assert "boom" in payload["exc_info"]


class TestEndToEnd:
    def test_phi_keyed_extra_redacted_in_json_output(self) -> None:
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(RedactPHIFilter())

        lg = logging.getLogger("e2e_test_phi")
        lg.handlers = [handler]
        lg.setLevel(logging.INFO)
        lg.propagate = False

        lg.info("saved note", extra={"patient_id": "abc-123", "latency_ms": 7})

        payload = json.loads(buf.getvalue().strip())
        assert payload["patient_id"] == "[REDACTED]"
        assert payload["latency_ms"] == 7

    def test_ssn_in_positional_format_scrubbed(self) -> None:
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(RedactPHIFilter())

        lg = logging.getLogger("e2e_test_ssn")
        lg.handlers = [handler]
        lg.setLevel(logging.INFO)
        lg.propagate = False

        lg.info("got ssn %s", "123-45-6789")

        payload = json.loads(buf.getvalue().strip())
        assert "123-45-6789" not in payload["message"]
        assert "[REDACTED-SSN]" in payload["message"]

    def test_configure_logging_is_idempotent(self) -> None:
        configure_logging(level="DEBUG")
        configure_logging(level="DEBUG")
        root = logging.getLogger()
        assert len(root.handlers) == 1


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)
    user_id_var.set(None)
    tenant_id_var.set(None)
    route_template_var.set(None)
