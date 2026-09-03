# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointment_types carries its own scheduling window

``appointment_types`` was a fee table: a name and a default fee. Everything
about WHEN an appointment could be offered lived practice-wide, so a
fifteen-minute consultation and a sixty-minute intake were held to the same
notice, the same lead time and the same horizon. They should not be.

Each type now carries:

* ``duration_minutes`` — how long it runs.
* ``audience`` — new patients, existing patients, or anyone.
* ``min_notice_hours`` — least notice, NULL meaning "use the practice
  default". NULL is deliberately distinct from 0, which means "none needed".
* ``earliest_offer_business_days`` — how far out the first offerable day is.
  0 allows same-day; 1 means "not today".
* ``horizon`` + ``horizon_unit`` — how far ahead, in working days or calendar
  days. "Ten business days" and "two weeks" are different promises, so the
  unit is stored rather than assumed.
* ``self_bookable`` — may a patient take this slot themselves. Off by default.
* ``offerable`` — may Pablo propose times for it. On by default.

Which DAYS count is not here and must not be: that comes from the availability
rules, and a type must never widen them.

Existing rows take the server defaults, which reproduce today's behaviour: a
50-minute existing-patient type, offered from the next working day up to ten
business days out, on the practice's default notice.

``appointments.session_type`` is untouched. It is still a free-form string
with no foreign key to this table; making it one needs a data migration and
is deliberately not bundled here.

Revision ID: c7e2a9f14b83
Revises: e5c9f2d73a18
Create Date: 2026-09-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7e2a9f14b83"
down_revision: str | Sequence[str] | None = "e5c9f2d73a18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "appointment_types"

# (name, type, nullable, server_default)
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[object], bool, str | None], ...] = (
    ("duration_minutes", sa.Integer(), False, "50"),
    ("audience", sa.String(length=10), False, "existing"),
    ("min_notice_hours", sa.Integer(), True, None),
    ("earliest_offer_business_days", sa.Integer(), False, "1"),
    ("horizon", sa.Integer(), False, "10"),
    ("horizon_unit", sa.String(length=10), False, "business"),
    ("self_bookable", sa.Boolean(), False, "false"),
    ("offerable", sa.Boolean(), False, "true"),
)

_CHECKS: tuple[tuple[str, str], ...] = (
    ("ck_appointment_types_duration", "duration_minutes BETWEEN 5 AND 480"),
    ("ck_appointment_types_audience", "audience IN ('new', 'existing', 'both')"),
    ("ck_appointment_types_min_notice", "min_notice_hours IS NULL OR min_notice_hours >= 0"),
    ("ck_appointment_types_earliest_offer", "earliest_offer_business_days >= 0"),
    ("ck_appointment_types_horizon", "horizon > 0"),
    ("ck_appointment_types_horizon_unit", "horizon_unit IN ('business', 'days')"),
)


def upgrade() -> None:
    for name, type_, nullable, default in _COLUMNS:
        op.add_column(
            _TABLE,
            sa.Column(name, type_, nullable=nullable, server_default=default),
        )
    for name, condition in _CHECKS:
        op.create_check_constraint(name, _TABLE, condition)


def downgrade() -> None:
    for name, _ in _CHECKS:
        op.drop_constraint(name, _TABLE, type_="check")
    for name, _, _, _ in reversed(_COLUMNS):
        op.drop_column(_TABLE, name)
