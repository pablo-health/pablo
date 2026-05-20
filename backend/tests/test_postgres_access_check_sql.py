# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test for THERAPY-255p — the ``has_patient_access`` SQL bind bug.

The Postgres repos call::

    text("SELECT has_patient_access(:pid::uuid, :uid)")

SQLAlchemy's text-bind parser does not handle a named parameter
adjacent to a ``::`` cast: it substitutes ``:uid`` but leaves
``:pid::uuid`` literal, and Postgres then rejects the statement with
a syntax error at ``:``. Switching the cast to ``CAST(:pid AS uuid)``
is the fix.

This test does not need a live DB — compiling each ``text(...)``
clause through the Postgres dialect surfaces the parsing bug at the
SQLAlchemy boundary.
"""

from __future__ import annotations

import inspect
import re

import pytest
from app.repositories.postgres import (
    appointment as appointment_mod,
)
from app.repositories.postgres import (
    chat as chat_mod,
)
from app.repositories.postgres import (
    note as note_mod,
)
from app.repositories.postgres import (
    patient as patient_mod,
)
from app.repositories.postgres import (
    patient_document as patient_document_mod,
)
from app.repositories.postgres import (
    session as session_mod,
)
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

_REPO_SOURCES = {
    "note": note_mod,
    "patient": patient_mod,
    "patient_document": patient_document_mod,
    "session": session_mod,
    "chat": chat_mod,
    "appointment": appointment_mod,
}

_ACCESS_SQL_RE = re.compile(r'text\(\s*"(SELECT has_patient_access\([^"]+\))"\s*\)')


@pytest.mark.parametrize(("name", "module"), list(_REPO_SOURCES.items()))
def test_has_patient_access_sql_binds_both_params(name: str, module) -> None:
    """Render the access-check SQL through the Postgres dialect and
    assert SQLAlchemy substituted *both* bind params.

    The original bug rendered ``:pid::uuid`` literally — psycopg2 then
    saw a bare ``:pid`` and failed. Asserting on the compiled string
    catches that regression without a live DB.
    """
    source = inspect.getsource(module)
    matches = _ACCESS_SQL_RE.findall(source)
    assert matches, f"no has_patient_access text() found in {name}"

    for sql in matches:
        stmt = text(sql).bindparams(pid="00000000-0000-0000-0000-000000000000", uid="u")
        compiled = str(
            stmt.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": False},
            )
        )
        assert ":pid" not in compiled, (
            f"{name}: SQLAlchemy left :pid unbound in {compiled!r} — "
            "this is the THERAPY-255p regression"
        )
        assert ":uid" not in compiled, f"{name}: :uid not bound in {compiled!r}"
