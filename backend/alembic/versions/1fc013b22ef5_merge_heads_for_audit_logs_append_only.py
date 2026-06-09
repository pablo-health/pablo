"""merge heads for audit_logs_append_only

Revision ID: 1fc013b22ef5
Revises: b33a493310b6, f1a8c63d49b2
Create Date: 2026-06-09 18:49:36.195086

"""
from collections.abc import Sequence



# Alembic reads the module-level globals below by name via runtime
# introspection. ``__all__`` marks them as intentional exports so static
# analyzers (github-code-quality, vulture, etc.) don't flag them as
# "unused global variable" — every migration in the repo carries the
# same shape.
__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

# revision identifiers, used by Alembic.
revision: str = '1fc013b22ef5'
down_revision: str | Sequence[str] | None = ('b33a493310b6', 'f1a8c63d49b2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
