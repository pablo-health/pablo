# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The row policy on ``patient_source_mappings``: the practice's answers, and the old ones.

A remembered answer belongs to the practice (``scope IS NOT NULL``), so any
clinician with a session armed reads and writes it: a feed code answered by
one clinician books for a colleague, and two followers of one calendar share
its answers. What such a row holds is a patient id, an answer and keyed
digests — nothing a practice member could not learn by matching.

A row from before that (``scope IS NULL``) carries the identifier in plain
text, so it stays its owner's alone until the app adopts it. Both arms
require an armed clinician: with nothing armed the table reads empty, like
every other tenant table.

One definition, applied at provisioning and on every migrate fan-out by
``enable_rls_on_schema``, and by the revision that adds the columns, so the
three never disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session

TABLE = "patient_source_mappings"
POLICY = "rls_practice_answers"
#: The policy this one replaces: owner-only, on ``user_id``.
OWNER_POLICY = "rls_user_isolation"

_ARMED = "coalesce(current_setting('app.current_user_id', true), '') <> ''"
_OWN = "user_id::text = current_setting('app.current_user_id', true)"
PREDICATE = f"((scope IS NOT NULL AND {_ARMED}) OR (scope IS NULL AND {_OWN}))"


def apply_practice_answers_policy(db: Session | Connection, schema: str) -> None:
    """Put the practice-answers policy on this schema's table, replacing the owner-only one.

    Idempotent. ``schema`` must already be validated by the caller.
    """
    qualified = f"{schema}.{TABLE}"
    db.execute(text(f"DROP POLICY IF EXISTS {OWNER_POLICY} ON {qualified}"))
    db.execute(text(f"DROP POLICY IF EXISTS {POLICY} ON {qualified}"))
    db.execute(
        text(f"CREATE POLICY {POLICY} ON {qualified} USING ({PREDICATE}) WITH CHECK ({PREDICATE})")
    )


def restore_owner_policy(db: Session | Connection, schema: str) -> None:
    """Put the owner-only policy back, for a downgrade."""
    qualified = f"{schema}.{TABLE}"
    db.execute(text(f"DROP POLICY IF EXISTS {POLICY} ON {qualified}"))
    db.execute(text(f"DROP POLICY IF EXISTS {OWNER_POLICY} ON {qualified}"))
    db.execute(
        text(f"CREATE POLICY {OWNER_POLICY} ON {qualified} USING ({_OWN}) WITH CHECK ({_OWN})")
    )


__all__ = [
    "OWNER_POLICY",
    "POLICY",
    "PREDICATE",
    "TABLE",
    "apply_practice_answers_policy",
    "restore_owner_policy",
]
