# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""intake_item_definitions: label and help_text

The wording of a question the practice wrote itself. Until now an item
carried a key, a type and that type's settings, and nowhere to put the
sentence the patient reads — so a free-text box or a set of choices had a
control and no question above it.

Columns rather than members of ``config`` because every type has one and
none of them varies by type. Both nullable: the questions the engine words
itself have nothing for a practice to write, a heading and a paragraph carry
their text in ``config``, and every row already stored is one of those. So
nothing is back-filled and no row is rewritten. Publishing is where a
question a practice wrote has to have a label.

The two file-backed types kept their wording in ``config['label']`` because
they were the only ones with anywhere to put it. That value moves to the
column here, and the key is dropped from the blob, so there is one place a
question's wording lives rather than two that can disagree.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: b7d4e05c318a
Revises: c5f80a214d9e
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7d4e05c318a"
down_revision: str | Sequence[str] | None = "d1b6a934f7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE intake_item_definitions ADD COLUMN IF NOT EXISTS label VARCHAR(300);")
    op.execute("ALTER TABLE intake_item_definitions ADD COLUMN IF NOT EXISTS help_text TEXT;")
    op.execute(
        "UPDATE intake_item_definitions "
        "SET label = left(config ->> 'label', 300), config = config - 'label' "
        "WHERE item_type IN ('insurance_card', 'document_request') "
        "AND config ? 'label';"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE intake_item_definitions "
        "SET config = jsonb_set(config, '{label}', to_jsonb(label)) "
        "WHERE item_type IN ('insurance_card', 'document_request') "
        "AND label IS NOT NULL;"
    )
    op.execute("ALTER TABLE intake_item_definitions DROP COLUMN IF EXISTS help_text;")
    op.execute("ALTER TABLE intake_item_definitions DROP COLUMN IF EXISTS label;")
