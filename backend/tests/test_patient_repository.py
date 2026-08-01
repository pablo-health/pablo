# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the Postgres patient repository's input guarding.

``patients.id`` is a uuid column, so a lookup by a non-uuid string would
raise a ``DataError`` at the SQL layer — surfacing to the caller as a 500.
A malformed id simply means "no such patient", so the repository resolves
it to a miss before issuing any query. A ``MagicMock`` session stands in
for a live database, mirroring ``TestPostgresNotesRepositoryMapping``.

Each repository call is made on its own line rather than inside the
``assert``, so the behavior under test still runs when assertions are
stripped (``python -O``).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.repositories.postgres.patient import PostgresPatientRepository


def test_get_with_non_uuid_id_returns_none_without_querying() -> None:
    """A malformed patient id short-circuits to ``None`` and never queries."""
    session = MagicMock()
    repo = PostgresPatientRepository(session)

    result = repo.get("not-a-uuid", "user-1")

    assert result is None
    session.execute.assert_not_called()


def test_get_with_valid_uuid_id_issues_query() -> None:
    """A syntactically valid uuid passes the guard and reaches the query."""
    session = MagicMock()
    session.execute.return_value.scalars.return_value.one_or_none.return_value = None
    repo = PostgresPatientRepository(session)

    result = repo.get(str(uuid.uuid4()), "user-1")

    assert result is None
    session.execute.assert_called_once()


def test_delete_with_non_uuid_id_returns_false_without_querying() -> None:
    """A malformed patient id short-circuits to ``False`` and never queries."""
    session = MagicMock()
    repo = PostgresPatientRepository(session)

    result = repo.delete("not-a-uuid", "user-1")

    assert result is False
    session.get.assert_not_called()


def test_restore_with_non_uuid_id_returns_none_without_querying() -> None:
    """A malformed patient id short-circuits to ``None`` and never queries."""
    session = MagicMock()
    repo = PostgresPatientRepository(session)

    result = repo.restore("not-a-uuid", "user-1")

    assert result is None
    session.get.assert_not_called()


def test_close_chart_with_non_uuid_id_returns_none_without_querying() -> None:
    """A malformed patient id short-circuits to ``None`` and never queries."""
    session = MagicMock()
    repo = PostgresPatientRepository(session)

    result = repo.close_chart("not-a-uuid", "user-1", None)

    assert result is None
    session.get.assert_not_called()


def test_reopen_chart_with_non_uuid_id_returns_none_without_querying() -> None:
    """A malformed patient id short-circuits to ``None`` and never queries."""
    session = MagicMock()
    repo = PostgresPatientRepository(session)

    result = repo.reopen_chart("not-a-uuid", "user-1")

    assert result is None
    session.get.assert_not_called()
