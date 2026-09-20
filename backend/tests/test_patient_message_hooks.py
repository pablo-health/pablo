# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The post-message callback seam.

Three properties, each one a bug somebody has already had: callbacks run in
registration order, a raising callback never takes the others (or the send)
down with it, and the failure log names a handle rather than a word the
patient wrote.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from app.services.patient_message_hooks import (
    PatientMessageEvent,
    dispatch_patient_message,
    get_patient_message_hook_registry,
)

_SECRET_BODY = "my panic attacks came back this week"
_SECRET_SUBJECT = "medication question"


@pytest.fixture(autouse=True)
def clean_registry():
    """No test may inherit another's callbacks, or leak one into the suite."""
    registry = get_patient_message_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


def _event(message_id: str = "message-1") -> PatientMessageEvent:
    return PatientMessageEvent(
        practice_schema="practice_test",
        patient_id="patient-a",
        thread_id="thread-1",
        message_id=message_id,
        sender="patient",
        subject=_SECRET_SUBJECT,
        body=_SECRET_BODY,
        created_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )


class _Recorder:
    def __init__(self, calls: list[str], label: str) -> None:
        self._calls = calls
        self._label = label

    def __call__(self, event: PatientMessageEvent) -> None:
        self._calls.append(f"{self._label}:{event.message_id}")


class _Exploder:
    def __call__(self, event: PatientMessageEvent) -> None:
        raise RuntimeError(f"downstream is down, and it says so with {event.body!r}")


def test_no_hooks_registered_is_the_default(clean_registry) -> None:
    """A self-hosted install gets plain messaging with nothing behind it."""
    assert clean_registry.hooks == ()
    dispatch_patient_message(_event())  # does not raise


def test_a_registered_hook_fires_once_with_the_payload(clean_registry) -> None:
    calls: list[str] = []
    clean_registry.register(_Recorder(calls, "one"))

    dispatch_patient_message(_event("message-7"))

    assert calls == ["one:message-7"]


def test_hooks_fire_in_registration_order(clean_registry) -> None:
    calls: list[str] = []
    clean_registry.register(_Recorder(calls, "first"))
    clean_registry.register(_Recorder(calls, "second"))

    dispatch_patient_message(_event())

    assert calls == ["first:message-1", "second:message-1"]


def test_a_raising_hook_does_not_stop_the_others_or_the_caller(clean_registry) -> None:
    """A patient's message must never bounce off something downstream."""
    calls: list[str] = []
    clean_registry.register(_Exploder())
    clean_registry.register(_Recorder(calls, "after"))

    dispatch_patient_message(_event())  # does not raise

    assert calls == ["after:message-1"]


def test_a_failure_logs_a_handle_and_not_a_word_of_the_message(
    clean_registry, caplog: pytest.LogCaptureFixture
) -> None:
    """Guardrail #5: the body is in the event, and stays out of the log."""
    clean_registry.register(_Exploder())

    with caplog.at_level(logging.WARNING):
        dispatch_patient_message(_event("message-9"))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "message-9" in logged
    assert "_Exploder" in logged
    assert _SECRET_BODY not in logged
    assert _SECRET_SUBJECT not in logged


def test_clear_isolates_tests(clean_registry) -> None:
    calls: list[str] = []
    clean_registry.register(_Recorder(calls, "gone"))
    clean_registry.clear()

    dispatch_patient_message(_event())

    assert calls == []
