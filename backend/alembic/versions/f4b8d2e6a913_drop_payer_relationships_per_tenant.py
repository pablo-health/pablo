# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Drop her payer relationships from each practice schema

The other half of ``a7c4e9b21f58`` on the platform chain, which created these
four in ``platform``. Same argument as ``e3a9c7b1d802`` made for the eleven
before them: Pablo runs credentialing centrally, so the operator reads a
clinician's panel status and the contract behind it across practices, and held
per-tenant that is a scan of every schema in the database.

SAFE NOW AND ONLY NOW. All four hold zero rows in every practice schema in both
environments, verified by heap size rather than ``count(*)`` — most of them are
force-RLS'd and the app role is ``NOBYPASSRLS``, so a count returns 0 whether
or not rows exist. Once the pilot files anything the same change becomes a data
migration.

THE DROP IS SCHEMA-QUALIFIED, DELIBERATELY. This chain runs once per practice
schema with ``search_path = <practice>, platform, public``, and the platform
schema now holds tables of exactly these names. An unqualified
``DROP TABLE IF EXISTS payer_participations`` in a practice schema that does
not have one would resolve through the search path and drop
``platform.payer_participations`` — and with it, by CASCADE, the events and the
rates that reference it. So each drop names ``current_schema()`` explicitly and
refuses to act on ``platform``.

CASCADE is deliberately absent. Within a practice schema the events and the
rates reference the participation, so dropping the parent first would need it.
Dropping children first removes the need, and that ordering is the point: a
CASCADE here would silently take anything else that had come to depend on these
tables, which is precisely what nobody wants a drop migration to decide on its
own.

Sits after ``f2a91c37b6d4`` rather than after ``e3a9c7b1d802``, whose argument
it borrows: two revisions landed on this chain after that one while this was in
flight, so it is no longer the head. Landing order is chain order, and a second
revision naming an already-parented revision is what leaves alembic with two
heads — which git merges without a word and every migration run then refuses.

Revision ID: f4b8d2e6a913
Revises: f2a91c37b6d4
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "f4b8d2e6a913"
down_revision: str | Sequence[str] | None = "f2a91c37b6d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Children before parents, so no CASCADE is needed. ``contracted_rates`` and
#: ``payer_participation_events`` both reference ``payer_participations``;
#: ``payer_authorizations`` references nothing and could go anywhere.
_TABLES = (
    "contracted_rates",
    "payer_participation_events",
    "payer_participations",
    "payer_authorizations",
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
    """Remove the four from this practice schema, and nowhere else."""
    for table in _TABLES:
        # ``replace`` on a constant rather than an f-string: the name comes from
        # the tuple above and never from a caller.
        op.execute(_DROP.replace("<table>", table))


def downgrade() -> None:
    """Deliberately does not recreate them.

    Same reasoning as ``e3a9c7b1d802``. Rebuilding four empty tables per
    practice schema would put the database back into the shape this revision
    exists to leave, while the platform copies — the ones that will hold the
    data once the pilot starts — stayed where they are. Two homes for one
    record is the state worth avoiding, so going back means reverting
    ``a7c4e9b21f58`` too, which is where the recreate belongs.

    A no-op rather than a raise: a chain-wide downgrade walking past this
    revision should not be stopped by it.
    """
