# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test for THERAPY-255p — has_patient_access bind safety.

Background: the original code used ``text("SELECT has_patient_access(:pid::uuid, :uid)")``.
SQLAlchemy's text-bind parser does not recognise ``:pid`` when it sits
adjacent to a Postgres ``::`` cast — it leaves ``:pid::uuid`` literal,
psycopg2 sends a bare ``:pid`` to the server, and Postgres rejects the
statement with a syntax error.

Current fix: each Postgres repo declares a module-level prepared
statement with **typed bindparams** (``Uuid``/``String``). SQLAlchemy's
postgres dialect renders the uuid bind with a dialect-side ``::UUID``
cast that is applied *after* parameter substitution — so the text-parser
never sees ``::`` adjacent to a bind name.

This test compiles each repo's ``_HAS_PATIENT_ACCESS_SQL`` constant
against the Postgres dialect and asserts both binds substitute cleanly.
Catches the bind-parser failure mode without a live database.
"""

from __future__ import annotations

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
from sqlalchemy.dialects import postgresql

_REPO_MODULES = {
    "note": note_mod,
    "patient": patient_mod,
    "patient_document": patient_document_mod,
    "session": session_mod,
    "chat": chat_mod,
    "appointment": appointment_mod,
}


@pytest.mark.parametrize(("name", "module"), list(_REPO_MODULES.items()))
def test_has_patient_access_stmt_compiles_with_both_binds(name: str, module) -> None:
    """Every Postgres repo exposes a typed prepared statement that
    compiles against the Postgres dialect with both ``:pid`` and ``:uid``
    substituted — and ``:pid`` is rendered with a dialect-side ``::UUID``
    cast so a text bind of a UUID-formatted string is sent as the right
    Postgres type.
    """
    stmt = module._HAS_PATIENT_ACCESS_SQL
    compiled = str(stmt.compile(dialect=postgresql.dialect()))

    # Both binds must render as psycopg2 placeholders; nothing should
    # leak through as a bare ``:pid`` or ``:uid`` (the THERAPY-255p
    # regression).
    assert ":pid" not in compiled, f"{name}: :pid not bound → {compiled!r}"
    assert ":uid" not in compiled, f"{name}: :uid not bound → {compiled!r}"
    assert "%(pid)s" in compiled, f"{name}: expected %(pid)s in {compiled!r}"
    assert "%(uid)s" in compiled, f"{name}: expected %(uid)s in {compiled!r}"

    # And the UUID column type must contribute a ``::UUID`` render cast
    # so Postgres receives the right argument type for has_patient_access.
    assert "::UUID" in compiled.upper(), (
        f"{name}: expected dialect-side ::UUID cast in {compiled!r}"
    )
