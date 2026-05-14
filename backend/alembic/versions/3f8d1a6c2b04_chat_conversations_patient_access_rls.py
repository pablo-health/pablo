# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Swap chat_conversations RLS to has_patient_access.

Extends the patient_clinicians access model from ``777b846ab944`` /
``9dea1edf7fe0`` to chat conversations. Before this migration,
``chat_conversations`` had no ``user_id`` column (it tracks
``owner_user_id`` instead, which the prior RLS bootstrap did not
detect), so the original ``enable_rls_on_schema`` left the table
without a policy. After PR #170's bootstrap rewrite, the same table
auto-receives the ``rls_patient_access`` policy keyed off
``patient_id`` — chat_conversations rows already carry a
``patient_id`` column, so the next ``enable_rls_on_schema`` pass
picks them up via the patient-id branch.

This migration is the bookkeeping companion for that swap: a defensive
``DROP POLICY IF EXISTS rls_user_isolation ON chat_conversations`` so
that any deployment that *did* manage to land an older user-id-style
policy (e.g. an experimental tenant where the bootstrap was patched
locally) cleanly transitions to the access-function policy. The
``IF EXISTS`` makes it a no-op on every other deployment.

Two clinical wins motivating the swap (matching the design doc):

  1. **Continuity across transfer / coverage.** When a patient
     transfers from clinician A to B, B inherits A's chat history
     (LLM-assisted reasoning about diagnosis, treatment options,
     etc.) rather than starting from scratch.

  2. **§ 164.312(a)(1) minimum-necessary.** When A loses the
     treatment relationship — primary clinician role transferred,
     coverage block expired — A simultaneously loses access to
     chats referencing the transferred patient's PHI.

``owner_user_id`` stays on the row as actor data ("Dr. X started
this chat") — same shape as ``therapy_sessions.user_id`` post-#170.
It is no longer the access proxy; the application-layer repository
and the database-level RLS policy both gate on
``has_patient_access(patient_id, current_user)``.

No data backfill is needed — every ``chat_conversations`` row already
has a ``patient_id``, and the ``patient_clinicians`` grants for those
patients were inserted by migration ``777b846ab944``.

Idempotent (``IF EXISTS``) so the per-tenant fan-out can re-run safely
across schemas that may or may not have the older policy.

Revision ID: 3f8d1a6c2b04
Revises: 9dea1edf7fe0
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# Alembic reads these module-level globals by name via runtime
# introspection. They are part of the migration's public contract; the
# ``__all__`` here marks them as intentional module exports so static
# analyzers don't flag them as "unused global variable."
__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "3f8d1a6c2b04"
down_revision: str | Sequence[str] | None = "9dea1edf7fe0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the legacy user-isolation policy if a prior bootstrap left one
    # in place. The new patient-access policy is created lazily by
    # ``app.db.enable_rls_on_schema`` on the next provisioning /
    # startup pass — chat_conversations has a ``patient_id`` column, so
    # the bootstrap's patient-id branch picks it up automatically and
    # installs ``rls_patient_access``. Keeping the policy creation in
    # the bootstrap (rather than this migration) means every tenant
    # schema goes through the same code path, including freshly
    # provisioned ones that never had the old policy.
    op.execute("DROP POLICY IF EXISTS rls_user_isolation ON chat_conversations")


def downgrade() -> None:
    # Recreate the same shape ``enable_rls_on_schema`` used to install
    # for tables with a user_id-style column — but chat_conversations
    # uses ``owner_user_id`` rather than ``user_id``, so the historical
    # bootstrap actually skipped it entirely. The most faithful
    # downgrade is therefore a no-op: dropping the patient-access
    # policy here would leave the table unprotected, and recreating an
    # owner-id policy with the historical bootstrap's column name
    # would mismatch reality. Operators who truly need to revert
    # should drop ``rls_patient_access`` manually after re-enabling
    # the old bootstrap path.
    pass
