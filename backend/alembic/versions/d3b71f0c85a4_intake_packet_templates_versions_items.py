# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""intake packet templates, versions and item definitions

The three per-tenant tables behind a practice-built intake form: a named
template, its numbered versions, and the ordered items on each version.
Storage and DDL only.

Practice-level rather than per-patient. A form is the practice's own
paperwork — the same questions whoever it is sent to — so none of the three
carries a ``patient_id`` and none gets a row-level policy. Their isolation
boundary is the tenant schema, which is why they are registered as
not-row-scoped in ``app.db``; forcing RLS on a table with no policy to write
would ship it as a silent deny-all. What a patient ANSWERS is a different
table, and that one is per-patient.

``item_type`` is constrained here as well as in ``app.intake.items`` because
the two constrain different things: the CHECK stops an unknown type reaching
the column at all, and the discriminated union stops a known type arriving
with the wrong settings. Neither substitutes for the other.

``key`` is unique per version and ``position`` is unique per version, for
two different reasons. Keys are what a visibility rule points at, so a
duplicate would make a rule ambiguous. Positions are the order the patient
reads, so a duplicate would make the form's order undefined.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: d3b71f0c85a4
Revises: a71c5e09d4b3
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3b71f0c85a4"
down_revision: str | Sequence[str] | None = "a71c5e09d4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_packet_templates (
            id           UUID         PRIMARY KEY,
            name         VARCHAR(120) NOT NULL,
            created_by   UUID,
            created_at   TIMESTAMPTZ  NOT NULL,
            archived_at  TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_packet_versions (
            id            UUID        PRIMARY KEY,
            template_id   UUID        NOT NULL,
            version       INTEGER     NOT NULL,
            published_at  TIMESTAMPTZ,
            published_by  UUID,
            created_at    TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_intake_packet_versions_template
                FOREIGN KEY (template_id)
                REFERENCES intake_packet_templates (id)
                ON DELETE CASCADE,
            CONSTRAINT uq_intake_packet_versions_number UNIQUE (template_id, version)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intake_packet_versions_template_id "
        "ON intake_packet_versions (template_id);"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_item_definitions (
            id                     UUID        PRIMARY KEY,
            version_id             UUID        NOT NULL,
            key                    VARCHAR(64) NOT NULL,
            position               INTEGER     NOT NULL,
            item_type              VARCHAR(32) NOT NULL,
            required               BOOLEAN     NOT NULL DEFAULT TRUE,
            config                 JSONB       NOT NULL DEFAULT '{}'::jsonb,
            resign_on_new_version  BOOLEAN     NOT NULL DEFAULT FALSE,
            CONSTRAINT fk_intake_item_definitions_version
                FOREIGN KEY (version_id)
                REFERENCES intake_packet_versions (id)
                ON DELETE CASCADE,
            CONSTRAINT ck_intake_item_definitions_type CHECK (item_type IN (
                'section','instructions','demographics','reason','free_text',
                'single_choice','multi_choice','yes_no','scale','number','date',
                'instrument','emergency_contact','guardian','consent_document',
                'insurance_card','document_request')),
            CONSTRAINT uq_intake_item_definitions_position UNIQUE (version_id, position),
            CONSTRAINT uq_intake_item_definitions_key UNIQUE (version_id, key)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intake_item_definitions_version_id "
        "ON intake_item_definitions (version_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS intake_item_definitions CASCADE;")
    op.execute("DROP TABLE IF EXISTS intake_packet_versions CASCADE;")
    op.execute("DROP TABLE IF EXISTS intake_packet_templates CASCADE;")
