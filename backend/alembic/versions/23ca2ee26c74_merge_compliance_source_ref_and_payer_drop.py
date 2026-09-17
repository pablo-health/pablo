# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Merge the compliance source-ref and payer-relationship-drop heads.

``a7d24e91f3c8`` and ``f4b8d2e6a913`` were written on separate branches
against the same parent and landed within half an hour of each other, so
the chain forked. Neither touches the other's tables, and this revision
carries no DDL of its own — it only gives the chain one head again.

Revision ID: 23ca2ee26c74
Revises: a7d24e91f3c8, f4b8d2e6a913
Create Date: 2026-09-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "23ca2ee26c74"
down_revision: str | Sequence[str] | None = ("a7d24e91f3c8", "f4b8d2e6a913")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
