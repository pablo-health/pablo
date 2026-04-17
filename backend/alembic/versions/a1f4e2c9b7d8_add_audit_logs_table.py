"""add audit_logs table

Creates the persistent HIPAA audit log table in the practice schema. Schema
is intentionally PHI-free — opaque IDs, action strings, timestamps. The
`changes` JSONB stores field-name diffs (no values) or non-PHI structured
data like counts and enum transitions. Previously audit entries only went
to stdout when Firestore was unavailable (§ 164.312(b) gap).

Revision ID: a1f4e2c9b7d8
Revises: d20c4753ded3
Create Date: 2026-04-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1f4e2c9b7d8"
down_revision: str | Sequence[str] | None = "d20c4753ded3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"], unique=False)
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], unique=False)
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_patient_id", "audit_logs", ["patient_id"], unique=False)
    # Composite index for the most common review query: "activity for user X in last N hours"
    op.create_index(
        "ix_audit_logs_user_timestamp",
        "audit_logs",
        ["user_id", sa.text("timestamp DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_user_timestamp", table_name="audit_logs")
    op.drop_index("ix_audit_logs_patient_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_user_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    op.drop_table("audit_logs")
