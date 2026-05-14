# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Merge ``a5f1c8d9e472`` (llm_usage_aggregate) and ``3f8d1a6c2b04`` (chat_conversations_patient_access_rls).

Pure graph-merge migration — no DDL. Two unrelated feature branches
landed in the same week without a merge migration between them, so
``alembic upgrade head`` died with ``MultipleHeads``:

  * ``a5f1c8d9e472`` (PR #167, LlmUsageMeter aggregate table) chained
    off ``c4e9a7b3f180``.
  * ``9dea1edf7fe0`` → ``3f8d1a6c2b04`` (PRs #170 + #173, patient
    access table + chat RLS swap) chained off the older
    ``777b846ab944``.

Neither chain references the other, so both stayed at ``(head)`` and
every downstream consumer that does ``command.upgrade(cfg, "head")``
broke — most visibly the SaaS overlay's ``saas.bin.migrate``, whose
integration tests have been red since the OSS base-image bumped to
``e42c0f0``.

This file is the standard Alembic remedy: declare a new revision
whose ``down_revision`` is the tuple ``(3f8d1a6c2b04, a5f1c8d9e472)``,
unifying the DAG into a single head. ``upgrade()`` / ``downgrade()``
are empty — nothing about the two branches conflicts at the SQL
level; they just need to share a successor in the graph.

Prevention follow-up: a ``poetry run alembic heads`` check in CI
would have caught the second head at PR time. Worth adding to the
backend ``lint`` job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]


revision: str = "65fdccbd8f77"
down_revision: str | Sequence[str] | None = ("3f8d1a6c2b04", "a5f1c8d9e472")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op — pure DAG merge."""


def downgrade() -> None:
    """No-op — pure DAG merge."""
