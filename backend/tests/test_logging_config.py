# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for structured JSON logging + PHI scrubber (THERAPY-za2y)."""

from __future__ import annotations

import io
import json
import logging
import re
import sys
from typing import TYPE_CHECKING, Any

import pytest
from app.logging_config import (
    MAX_RECORD_BYTES,
    JSONFormatter,
    RedactPHIFilter,
    configure_logging,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping


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

    def test_exception_traceback_phi_shape_scrubbed(self) -> None:
        # A wrapped exception whose message echoes a PHI-shaped value (here an
        # SSN) must not reach the sink verbatim via the traceback — the formatter
        # scrubs exc_info on the same Layer-2 shapes as the message.
        try:
            raise ValueError("import failed for 123-45-6789")
        except ValueError:
            rec = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="note import failed",
                args=None,
                exc_info=sys.exc_info(),
            )
        payload = json.loads(self.fmt.format(rec))
        assert "123-45-6789" not in payload["exc_info"]
        assert "[REDACTED-SSN]" in payload["exc_info"]


def _raise_innermost(message: str, cause: BaseException | None = None) -> None:
    if cause is not None:
        raise RuntimeError(message) from cause
    raise ValueError(message)


def _descend_even(depth: int, message: str, cause: BaseException | None = None) -> None:
    if depth <= 0:
        _raise_innermost(message, cause)
    _descend_odd(depth - 1, message, cause)


def _descend_odd(depth: int, message: str, cause: BaseException | None) -> None:
    # Two functions calling each other, so consecutive frames differ and the
    # traceback prints every one of them (no "Previous line repeated").
    _descend_even(depth - 1, message, cause)


def _raise_chained(depth: int) -> None:
    """``raise RuntimeError from ValueError``, both ``depth`` frames deep."""
    try:
        _descend_even(depth, "inner cause")
    except ValueError as cause:
        _descend_even(depth, "outer failure", cause)


def _log_exception(fn: Any, *args: Any) -> str:
    """Raise through ``fn`` and log it with logger.exception; return the line."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())
    logger = logging.getLogger("test.bounded")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        fn(*args)
    except Exception:
        logger.exception("pablo.unhandled")
    line = stream.getvalue()
    assert line.endswith("\n")
    assert line.count("\n") == 1
    return line[:-1]


_FRAME_FILE_RE = re.compile(r'^  File "')


def _file_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if _FRAME_FILE_RE.match(line)]


class TestRecordBound:
    """A record never exceeds MAX_RECORD_BYTES because of its traceback."""

    def test_deep_traceback_keeps_header_innermost_frames_and_exception_line(self) -> None:
        line = _log_exception(_descend_even, 300, "deep failure")
        assert len(line.encode("utf-8")) <= MAX_RECORD_BYTES
        payload = json.loads(line)

        exc_info = payload["exc_info"]
        assert payload["stack_trace"] == exc_info
        assert payload["exc_info_truncated"] is True
        assert exc_info.startswith("Traceback (most recent call last):\n")
        omitted = payload["exc_info_frames_omitted"]
        assert omitted > 0
        assert f"\n  [{omitted} outer frames omitted]\n" in exc_info
        assert "in _raise_innermost" in exc_info
        assert exc_info.endswith("\nValueError: deep failure")
        assert payload["message"] == "pablo.unhandled"
        assert payload["error_class"] == "ValueError"

    def test_marker_is_not_a_frame(self) -> None:
        payload = json.loads(_log_exception(_descend_even, 300, "deep failure"))
        marker = next(line for line in payload["exc_info"].split("\n") if "omitted]" in line)
        assert marker.startswith("  [")
        assert not _FRAME_FILE_RE.match(marker)

    def test_kept_frames_are_the_innermost_in_original_order(self) -> None:
        try:
            _descend_even(300, "deep failure")
        except ValueError:
            original = _file_lines(JSONFormatter().formatException(sys.exc_info()))
        payload = json.loads(_log_exception(_descend_even, 300, "deep failure"))
        kept = _file_lines(payload["exc_info"])
        assert 0 < len(kept) < len(original)
        assert kept == original[-len(kept) :]
        assert payload["exc_info_frames_omitted"] == len(original) - len(kept)

    def test_trims_no_more_frames_than_needed(self) -> None:
        line = _log_exception(_descend_even, 300, "deep failure")
        # Frames go one at a time from the outer end until the line fits, so
        # it lands within about one frame (written twice: exc_info and
        # stack_trace) of the bound, not at the innermost frame alone.
        assert MAX_RECORD_BYTES - 600 < len(line) <= MAX_RECORD_BYTES
        assert len(_file_lines(json.loads(line)["exc_info"])) > 10

    def test_chained_exception_drops_the_earlier_segment_first(self) -> None:
        line = _log_exception(_raise_chained, 200)
        assert len(line.encode("utf-8")) <= MAX_RECORD_BYTES
        payload = json.loads(line)
        exc_info = payload["exc_info"]
        assert payload["stack_trace"] == exc_info
        assert exc_info.startswith("[1 earlier chained traceback(s) omitted]\n")
        assert "inner cause" not in exc_info
        assert "The above exception was the direct cause" not in exc_info
        assert "\nTraceback (most recent call last):\n" in exc_info
        assert exc_info.endswith("\nRuntimeError: outer failure")
        # The dropped segment's ~200 frames are counted with the outer ones.
        assert payload["exc_info_frames_omitted"] > 200

    def test_huge_exception_message_is_truncated(self) -> None:
        line = _log_exception(_raise_innermost, "x" * 40_000)
        assert len(line.encode("utf-8")) <= MAX_RECORD_BYTES
        payload = json.loads(line)
        exc_info = payload["exc_info"]
        assert payload["stack_trace"] == exc_info
        assert payload["exc_info_truncated"] is True
        # Frame trimming runs first and leaves only the innermost frame
        # (the caller's frame is the one dropped); then the message is cut.
        assert payload["exc_info_frames_omitted"] == 1
        assert exc_info.startswith(
            "Traceback (most recent call last):\n  [1 outer frames omitted]\n"
        )
        assert _file_lines(exc_info)[-1].endswith("in _raise_innermost")
        assert "\nValueError: xxx" in exc_info
        assert exc_info.endswith("x... [truncated]")

    def test_short_exception_record_is_unchanged(self) -> None:
        line = _log_exception(_descend_even, 3, "short failure")
        payload = json.loads(line)
        assert "exc_info_truncated" not in payload
        assert "exc_info_frames_omitted" not in payload
        # Byte for byte what the formatter has always written: the payload
        # serialized compactly, keys in insertion order, nothing re-ordered.
        assert line == json.dumps(payload, default=str, separators=(",", ":"))
        assert "omitted]" not in payload["exc_info"]

    def test_long_record_without_exception_is_unchanged(self) -> None:
        fmt = JSONFormatter()
        rec = _make_record("op", extra={"detail": "y" * 40_000})
        line = fmt.format(rec)
        assert len(line) > MAX_RECORD_BYTES
        payload = json.loads(line)
        assert payload["detail"] == "y" * 40_000
        assert "exc_info_truncated" not in payload

    def test_warning_with_exception_has_no_stack_trace_and_is_bounded(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JSONFormatter())
        logger = logging.getLogger("test.bounded.warning")
        logger.handlers = [handler]
        logger.propagate = False
        try:
            _descend_even(300, "deep failure")
        except ValueError:
            logger.warning("retrying", exc_info=True)
        line = stream.getvalue().rstrip("\n")
        assert len(line) <= MAX_RECORD_BYTES
        payload = json.loads(line)
        assert "stack_trace" not in payload
        assert payload["exc_info_truncated"] is True
        assert payload["exc_info"].endswith("\nValueError: deep failure")


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

    @staticmethod
    def _emit(name: str, msg: str, *args: object, **kwargs: Any) -> dict[str, Any]:
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JSONFormatter())
        handler.addFilter(RedactPHIFilter())
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(logging.INFO)
        lg.propagate = False
        lg.error(msg, *args, **kwargs)
        payload: dict[str, Any] = json.loads(buf.getvalue().strip())
        return payload

    def test_format_string_emitted_as_message_template(self) -> None:
        payload = self._emit("e2e_test_template", "lookup failed for %s", "x")
        assert payload["message"] == "lookup failed for x"
        assert payload["message_template"] == "lookup failed for %s"

    def test_mapping_args_emit_message_template(self) -> None:
        payload = self._emit("e2e_test_template_map", "lookup failed for %(key)s", {"key": "x"})
        assert payload["message"] == "lookup failed for x"
        assert payload["message_template"] == "lookup failed for %(key)s"

    def test_no_message_template_without_args(self) -> None:
        value = "x"
        formatted = self._emit("e2e_test_template_fstr", f"lookup failed for {value}")
        assert formatted["message"] == "lookup failed for x"
        assert "message_template" not in formatted
        plain = self._emit("e2e_test_template_plain", "no args")
        assert plain["message"] == "no args"
        assert "message_template" not in plain

    def test_extra_cannot_replace_message_template(self) -> None:
        payload = self._emit(
            "e2e_test_template_extra",
            "lookup failed for %s",
            "x",
            extra={"message_template": "other"},
        )
        assert payload["message_template"] == "lookup failed for %s"

    def test_scrubbed_message_carries_no_template(self) -> None:
        # The scrub replaces the message and drops the arguments; the line
        # then has no format string to report.
        payload = self._emit("e2e_test_template_ssn", "got ssn %s", "123-45-6789")
        assert "[REDACTED-SSN]" in payload["message"]
        assert "message_template" not in payload
        assert "123-45-6789" not in json.dumps(payload)

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
