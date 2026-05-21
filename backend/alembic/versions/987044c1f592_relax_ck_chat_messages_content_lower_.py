# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""relax ck_chat_messages_content_len to allow empty placeholder rows

Background: ``chat_turn_service`` inserts an assistant placeholder row
with ``content=''`` at the start of every turn, then updates it in
place at end-of-stream (forensic-row pattern). The original
constraint's lower bound of 1 made this INSERT crash on every chat
request against a real Postgres. Service code already coerces empty
final content to ``'[no output]'`` at ``chat_turn_service.py:353``, so
the placeholder state is the only legitimate length-0 case. Upper
bound unchanged.

See THERAPY-1cqc for full root-cause notes.

Revision ID: 987044c1f592
Revises: a6af3834b782
Create Date: 2026-05-20 21:02:59.304651
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "987044c1f592"
down_revision: str | Sequence[str] | None = "a6af3834b782"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_messages_content_len", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_content_len",
        "chat_messages",
        "char_length(content) BETWEEN 0 AND 32768",
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_messages_content_len", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_content_len",
        "chat_messages",
        "char_length(content) BETWEEN 1 AND 32768",
    )
