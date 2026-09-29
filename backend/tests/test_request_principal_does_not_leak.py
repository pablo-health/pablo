# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""A test that arms a request principal and never resets it does not hand it
to the next test.

The two tests below run in file order. The first arms all three request
ContextVars and deliberately leaves them set; the second asserts they are
back to what they were before the first began. What makes it so is the
shared guard in ``backend/conftest.py`` — this module does no cleanup of its
own, on purpose.
"""

from __future__ import annotations

from typing import Any

from app.db import (
    _current_patient_id,
    _current_tenant_schema,
    _current_user_id,
    set_current_patient_id,
    set_current_user_id,
)

_ARMED_USER = "0b3f5d2e-7c1a-4e9b-8d6f-2a4c6e8f0b1d"
_ARMED_PATIENT = "9e7c5a3b-1d2f-4a6b-8c0e-f1a3b5c7d9e2"
_ARMED_SCHEMA = "practice_leak_probe"

#: What the three held before the leaking test ran, filled in by it.
_BEFORE: dict[str, Any] = {}


def test_1_arms_every_principal_and_leaves_them_set() -> None:
    _BEFORE.update(
        user=_current_user_id.get(),
        patient=_current_patient_id.get(),
        schema=_current_tenant_schema.get(),
    )

    set_current_user_id(_ARMED_USER)
    set_current_patient_id(_ARMED_PATIENT)
    _current_tenant_schema.set(_ARMED_SCHEMA)

    # Armed for as long as this test runs — restoring must not blank them early.
    assert _current_user_id.get() == _ARMED_USER
    assert _current_patient_id.get() == _ARMED_PATIENT
    assert _current_tenant_schema.get() == _ARMED_SCHEMA


def test_2_the_next_test_starts_from_what_was_there_before() -> None:
    assert _BEFORE, "the arming test did not run first"
    assert _current_user_id.get() == _BEFORE["user"]
    assert _current_patient_id.get() == _BEFORE["patient"]
    assert _current_tenant_schema.get() == _BEFORE["schema"]
    assert _current_user_id.get() != _ARMED_USER
    assert _current_patient_id.get() != _ARMED_PATIENT
