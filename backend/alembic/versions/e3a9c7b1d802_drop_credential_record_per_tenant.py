# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop the credential record from each practice schema

The other half of ``b6e2f8a41c37`` on the platform chain, which created these
eleven tables in ``platform``. Pablo runs credentialing as a concierge service,
so the operator reads a clinician's licences and disclosures across practices;
held per-tenant that was a scan of every schema in the database to answer a
question about one person. ``panel_applications`` moved for the same reason in
``c4d81e6a2f09``.

SAFE NOW AND ONLY NOW. Every one of these holds zero rows, in every schema, in
both environments — measured before writing this, not assumed. The surface was
built but never used, so there is nothing to carry across and no window in
which a row could be lost. Once the concierge pilot files anything, the same
change becomes a data migration.

THE DROP IS SCHEMA-QUALIFIED, DELIBERATELY. This chain runs once per practice
schema with ``search_path = <practice>, platform, public``, and the platform
schema now holds tables of exactly these names. An unqualified
``DROP TABLE IF EXISTS credential_licenses`` in a practice schema that does not
have one would therefore resolve through the search path and drop
``platform.credential_licenses`` — the table the other half just created, with
whatever it holds. ``c4d81e6a2f09`` is written unqualified and got away with it
because it recreated the platform table in the same pass; this one has no such
second chance, because the platform side lives in a different chain that has
already run.

So each drop names ``current_schema()`` explicitly and refuses to act on
``platform``. The belt is the qualification; the braces are the guard.

Revision ID: e3a9c7b1d802
Revises: d8f3b6c04e17
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "e3a9c7b1d802"
down_revision: str | Sequence[str] | None = "d8f3b6c04e17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The eleven that left. Names only — the DDL that recreates them on downgrade
#: is deliberately absent; see ``downgrade``.
_TABLES = (
    "credential_bank_accounts",
    "credential_confirmations",
    "credential_disclosures",
    "credential_education",
    "credential_employment",
    "credential_government_ids",
    "credential_liability_policies",
    "credential_licenses",
    "credential_references",
    "credential_service_locations",
    "credential_training",
)

#: Drop the table from THIS schema and no other. ``current_schema()`` is the
#: first entry of the search path, which the tenant env sets to the practice
#: being migrated. The ``platform`` guard makes the mistake unrepresentable
#: rather than merely unlikely.
_DROP = """
DO $$
DECLARE
    schema_name text := current_schema();
BEGIN
    IF schema_name IS NULL OR schema_name = 'platform' THEN
        RAISE NOTICE 'refusing to drop <table> outside a practice schema (%)', schema_name;
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = schema_name AND table_name = '<table>'
    ) THEN
        EXECUTE format('DROP TABLE %I.<table>', schema_name);
    END IF;
END
$$
"""


def upgrade() -> None:
    """Remove the eleven from this practice schema, and nowhere else."""
    for table in _TABLES:
        # ``replace`` on a constant rather than an f-string: the name comes from
        # the tuple above and never from a caller.
        op.execute(_DROP.replace("<table>", table))


def downgrade() -> None:
    """Deliberately does not recreate them.

    A downgrade that rebuilt eleven empty tables per practice schema would put
    the database back into the shape this revision exists to leave — and the
    platform copies, which are the ones with the data once the pilot starts,
    would still be there. Two homes for one record is the state worth avoiding,
    so going back means reverting the platform revision too (``b6e2f8a41c37``),
    which is where the recreate belongs.

    Left as a no-op rather than raising: a chain-wide downgrade that walks past
    this revision should not be stopped by it.
    """
