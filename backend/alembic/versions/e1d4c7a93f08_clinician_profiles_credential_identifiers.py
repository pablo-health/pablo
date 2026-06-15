# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""clinician_profiles gains dea_number / npi_number

A prescriber's DEA registration number and NPI are stable credential
identifiers that were previously only ever captured per-encounter (and
re-typed each time). Store them on the clinician profile — alongside
``license_number`` / ``license_state`` — so prescribing surfaces can
read them once instead of re-collecting them. They are regulatory
identifiers, not PHI, and are per-practice for the same reason the
license fields are (a clinician may hold a different registration at a
different practice).

Unqualified table name resolves via search_path to the active practice
schema (the ``practice`` template at deploy time, each tenant during the
per-tenant fan-out). ``ADD COLUMN IF NOT EXISTS`` makes the statement a
no-op on schemas that already have the columns, so it is safe to re-run
under the fan-out.

Revision ID: e1d4c7a93f08
Revises: 9f4c1a7b2e60
Create Date: 2026-06-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "e1d4c7a93f08"
down_revision: str | Sequence[str] | None = "9f4c1a7b2e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE clinician_profiles ADD COLUMN IF NOT EXISTS dea_number VARCHAR(50)")
    op.execute("ALTER TABLE clinician_profiles ADD COLUMN IF NOT EXISTS npi_number VARCHAR(20)")


def downgrade() -> None:
    op.execute("ALTER TABLE clinician_profiles DROP COLUMN IF EXISTS npi_number")
    op.execute("ALTER TABLE clinician_profiles DROP COLUMN IF EXISTS dea_number")
