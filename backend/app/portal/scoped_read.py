# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One read, as one principal, before anybody has signed in.

Two portal routes have to read a chart while nobody holds a session: asking
for a sign-in code (where to text it) and account recovery (whose chart an
address belongs to). ``patients`` is row-scoped and the runtime role does not
bypass row-level security, so an unarmed read sees no rows at all.

Arming a principal for the rest of the request is the wrong fix. The note on
the challenge tables in ``app.db`` rejects it for the sign-in path because it
would scope the session to someone who has not completed step-up, and on an
unauthenticated route whatever ran after it would inherit that scope.

So :func:`read_as` gives a principal exactly one statement. The setting is
made with ``set_config(..., true)`` inside a savepoint and the savepoint is
rolled back afterwards. Postgres reverts a transaction-local setting along
with the savepoint it was made in, and nothing is written to
``session.info`` or the request ContextVars, so the next statement runs as
unscoped as the one before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Executable, Row
    from sqlalchemy.orm import Session

#: The two principals a policy can be armed for. A literal rather than a free
#: string, so no caller can name some other setting.
type Principal = Literal["app.current_patient_id", "app.current_user_id"]


def read_as(
    session: Session, *, principal: Principal, value: str, statement: Executable
) -> Sequence[Row[Any]]:
    """Run *statement* with *principal* set to *value*, and nothing after it."""
    savepoint = session.begin_nested()
    try:
        session.execute(
            text("SELECT set_config(:setting, :value, true)"),
            {"setting": principal, "value": value},
        )
        return session.execute(statement).all()
    finally:
        savepoint.rollback()
