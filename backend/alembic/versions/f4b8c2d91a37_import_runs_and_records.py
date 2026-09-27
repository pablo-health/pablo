# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""import_runs and import_records tables

The ledger behind importing a records-system export into a practice.

``import_runs`` is one row per run: which source system, what scope, who
started it, where it got to, and a report of counts and handles. It is
practice data, not patient data — it names no patient and holds no content —
so it is registered not-row-scoped in ``app.db`` and its isolation boundary
is the tenant schema.

``import_records`` is one row per source record that was landed, keyed by
the source system's own record id. That key is what makes a second run of
the same archive exact: a source id seen before with the same digest is
skipped, a changed one is updated only if the target has not been edited in
Pablo since, and undo knows every row a run created. ``previous_payload``
holds the pre-update snapshot for records a run changed, so undo can put
them back. The table has none of the columns the RLS pre-flight keys on
(``id``, ``user_id``, ``patient_id``) — ``target_id`` is a plain reference —
so it needs no policy and no registration.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: f4b8c2d91a37
Revises: e7a3f9b2d4c6
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4b8c2d91a37"
down_revision: str | Sequence[str] | None = "e7a3f9b2d4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_runs (
            id              UUID         PRIMARY KEY,
            source_system   VARCHAR(40)  NOT NULL,
            scope           VARCHAR(16)  NOT NULL,
            state           VARCHAR(16)  NOT NULL,
            started_by      UUID         NOT NULL,
            started_at      TIMESTAMPTZ  NOT NULL,
            finished_at     TIMESTAMPTZ,
            archive_ref     TEXT,
            archive_expires_at TIMESTAMPTZ,
            preview         JSONB,
            decisions       JSONB,
            counts          JSONB,
            report          JSONB,
            error           TEXT,
            CONSTRAINT ck_import_runs_scope
                CHECK (scope IN ('patients','practice','both')),
            CONSTRAINT ck_import_runs_state
                CHECK (state IN ('queued','previewing','previewed','applying','applied',
                                 'undoing','undone','failed'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_import_runs_started_at ON import_runs (started_at DESC);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS import_records (
            source_system    VARCHAR(40)  NOT NULL,
            record_type      VARCHAR(40)  NOT NULL,
            source_id        VARCHAR(200) NOT NULL,
            target_table     VARCHAR(64)  NOT NULL,
            target_id        UUID         NOT NULL,
            source_digest    VARCHAR(64)  NOT NULL,
            run_id           UUID         NOT NULL,
            state            VARCHAR(16)  NOT NULL,
            previous_payload JSONB,
            imported_at      TIMESTAMPTZ  NOT NULL,
            updated_at       TIMESTAMPTZ  NOT NULL,
            CONSTRAINT pk_import_records PRIMARY KEY (source_system, record_type, source_id),
            CONSTRAINT ck_import_records_state
                CHECK (state IN ('landed','updated','undone')),
            CONSTRAINT fk_import_records_run
                FOREIGN KEY (run_id) REFERENCES import_runs (id)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_import_records_run ON import_records (run_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_import_records_target "
        "ON import_records (target_table, target_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS import_records;")
    op.execute("DROP TABLE IF EXISTS import_runs;")
