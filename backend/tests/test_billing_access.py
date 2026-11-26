# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who may bill for the practice, and the read that refuses everyone else.

The read itself is proven against Postgres in
``tests_integration/database/practice_directory/test_claim_source.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from app.billing_access import can_bill, read_claim_source
from app.db.platform_models import PracticeRow
from app.models import User

if TYPE_CHECKING:
    from app.repositories.practice_admin import InMemoryPracticeAdminRepository

_APPOINTMENT = "5d1c7a40-2b9e-5f63-8c14-7a0e3b6d9f21"


def _user(user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        name="Someone",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


OWNER = _user("11111111-1111-1111-1111-111111111111", "owner@example.com")
ADMIN = _user("22222222-2222-2222-2222-222222222222", "admin@example.com")
CLINICIAN = _user("33333333-3333-3333-3333-333333333333", "clinician@example.com")
PRACTICE = PracticeRow(id="practice-1", owner_email="Owner@Example.com")


def test_the_owner_and_a_practice_admin_may_bill_and_a_clinician_may_not(
    practice_admins: InMemoryPracticeAdminRepository,
) -> None:
    practice_admins.grant(ADMIN.id, OWNER.id, datetime(2026, 1, 1, tzinfo=UTC))

    assert can_bill(PRACTICE, OWNER)
    assert can_bill(PRACTICE, ADMIN)
    assert not can_bill(PRACTICE, CLINICIAN)


def test_the_read_refuses_whoever_may_not_bill_without_touching_the_database() -> None:
    db = MagicMock()

    assert read_claim_source(db, PRACTICE, CLINICIAN, _APPOINTMENT) is None
    db.execute.assert_not_called()


def test_the_read_treats_a_malformed_id_as_a_miss() -> None:
    db = MagicMock()

    assert read_claim_source(db, PRACTICE, OWNER, "not-a-uuid") is None
    db.execute.assert_not_called()
