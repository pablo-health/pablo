# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Owner-scoped tables: the registry entry type and the one policy each gets."""

from __future__ import annotations

from typing import NamedTuple


class OwnerScope(NamedTuple):
    """How one owner-scoped tenant table names the user a row belongs to."""

    #: The column holding the owning user's id, compared as text.
    owner_column: str
    #: Rows whose owner column is NULL are shared by every armed user.
    shared_when_null: bool


#: The policy every owner-scoped table gets. A name of its own, so the drop
#: list in ``_plan_row_scoped_table`` can shed it from a table that is no
#: longer registered without touching anything else's policy.
OWNER_POLICY_NAME = "rls_owner_isolation"

#: A user principal is armed. Same idiom as ``practice_answers``: a session
#: that never set the setting reads NULL, which ``coalesce`` turns into ``''``.
_USER_ARMED = "coalesce(current_setting('app.current_user_id', true), '') <> ''"


def _refuse_owner_and_not_row_scoped(owner_scoped: set[str], not_row_scoped: set[str]) -> None:
    """Raise for a table registered both owner-scoped and not-row-scoped.

    The not-row-scoped branch wins in ``infer_rls_on_schema``'s loop, so the
    owner policy the deployment asked for would never be created. A table is
    one or the other, and which registration is wrong is not ours to guess.
    """
    both = owner_scoped & not_row_scoped
    if both:
        raise RuntimeError(
            f"infer_rls_on_schema: {sorted(both)} registered both owner-scoped and "
            f"not-row-scoped. A table is one or the other — refusing to guess which "
            f"registration is wrong."
        )


def _owner_policy_statement(qualified: str, scope: OwnerScope) -> str:
    """The ``rls_owner_isolation`` policy for one owner-scoped table.

    ``owner_column`` reaches the string only after the caller checked it is a
    column of the live table, so it is an existing identifier, never input.
    It is quoted all the same, so a mixed-case or reserved-word column name
    still makes valid SQL.

    Both arms require an armed principal. A pooled connection that once ran
    ``set_config(..., true)`` reads the setting back as ``''``, not NULL,
    after that transaction ends, so without the armed test an unarmed session
    would match every row whose owner column holds an empty string.
    """
    column = '"' + scope.owner_column.replace('"', '""') + '"'
    predicate = f"{column}::text = current_setting('app.current_user_id', true) AND {_USER_ARMED}"
    if scope.shared_when_null:
        predicate = f"({predicate}) OR ({column} IS NULL AND {_USER_ARMED})"
    return (
        f"CREATE POLICY {OWNER_POLICY_NAME} ON {qualified} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )
