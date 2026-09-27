# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The refill-request callback seam.

The same three properties as the secure-messaging seam: registration order
is kept, a raising callback never takes the others down, and the failure
log names a handle rather than the medication or the patient's note.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from app.services.refill_request_hooks import (
    RefillRequestEvent,
    dispatch_refill_request_event,
    get_refill_request_hook_registry,
)

_SECRET_MEDICATION = "Clonazepam 0.5 mg"
_SECRET_NOTE = "I took extra last week"


@pytest.fixture(autouse=True)
def clean_registry():
    registry = get_refill_request_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


def _event(request_id: str = "request-1") -> RefillRequestEvent:
    return RefillRequestEvent(
        kind="submitted",
        practice_schema="practice_test",
        request_id=request_id,
        patient_id="patient-a",
        status="requested",
        medication_text=_SECRET_MEDICATION,
        patient_note=_SECRET_NOTE,
        created_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
    )


class _Exploder:
    def __call__(self, event: RefillRequestEvent) -> None:
        raise RuntimeError(f"downstream is down: {event.medication_text} {event.patient_note}")


def test_no_hooks_registered_is_the_default(clean_registry) -> None:
    assert clean_registry.hooks == ()
    dispatch_refill_request_event(_event())  # does not raise


def test_hooks_fire_in_registration_order(clean_registry) -> None:
    calls: list[str] = []
    clean_registry.register(lambda e: calls.append(f"first:{e.request_id}"))
    clean_registry.register(lambda e: calls.append(f"second:{e.request_id}"))

    dispatch_refill_request_event(_event())

    assert calls == ["first:request-1", "second:request-1"]


def test_a_raising_hook_does_not_stop_the_others(clean_registry) -> None:
    calls: list[str] = []
    clean_registry.register(_Exploder())
    clean_registry.register(lambda e: calls.append(e.request_id))

    dispatch_refill_request_event(_event())

    assert calls == ["request-1"]


def test_a_failure_logs_a_handle_and_nothing_the_patient_wrote(
    clean_registry, caplog: pytest.LogCaptureFixture
) -> None:
    clean_registry.register(_Exploder())

    with caplog.at_level(logging.WARNING):
        dispatch_refill_request_event(_event("request-9"))

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "request-9" in logged
    assert "_Exploder" in logged
    assert _SECRET_MEDICATION not in logged
    assert _SECRET_NOTE not in logged
