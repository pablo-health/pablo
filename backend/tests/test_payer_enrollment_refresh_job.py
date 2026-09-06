# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The daily enrollment refresh job (``app.jobs.payer_enrollment_refresh``).

What matters: the run is bounded by ``--max-tenants`` and hands
``--max-per-tenant`` down; each practice is refreshed in its own
tenant-scoped session; a practice without a clearinghouse is skipped
without a session; one practice's vendor error does not stop the next.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.jobs import payer_enrollment_refresh as job

_REGISTRY = [
    ("practice_alpha", "alpha"),
    ("practice_beta", "beta"),
    ("practice_gamma", "gamma"),
    ("practice_delta", "delta"),
]


class _Session:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    sessions: dict[str, _Session] = {}
    refreshed: list[tuple[str, int]] = []
    clients = {practice_id: object() for _schema, practice_id in _REGISTRY}

    def create_standalone_session(schema: str) -> _Session:
        sessions[schema] = _Session()
        return sessions[schema]

    def refresh_enrollments(session: _Session, client: object, *, limit: int) -> int:
        schema = next(s for s, sess in sessions.items() if sess is session)
        refreshed.append((schema, limit))
        if schema == "practice_beta":
            raise ClearinghouseUnavailableError("down")
        return 2

    monkeypatch.setattr(job, "get_engine", object)
    monkeypatch.setattr(job, "list_active_practice_registry", lambda _engine: list(_REGISTRY))
    monkeypatch.setattr(job, "get_clearinghouse_client", clients.get)
    monkeypatch.setattr(job, "create_standalone_session", create_standalone_session)
    monkeypatch.setattr(job, "refresh_enrollments", refresh_enrollments)
    return {"sessions": sessions, "refreshed": refreshed, "clients": clients}


def test_visits_every_practice_in_its_own_session(harness: dict[str, Any]) -> None:
    assert job.run([]) == 0

    assert [schema for schema, _ in harness["refreshed"]] == [s for s, _ in _REGISTRY]
    assert all(session.closed for session in harness["sessions"].values())


def test_bounded_by_max_tenants_and_hands_down_the_per_tenant_cap(
    harness: dict[str, Any],
) -> None:
    assert job.run(["--max-tenants", "2", "--max-per-tenant", "7"]) == 0

    assert harness["refreshed"] == [("practice_alpha", 7), ("practice_beta", 7)]


def test_default_per_tenant_cap_is_the_modules(harness: dict[str, Any]) -> None:
    job.run(["--max-tenants", "1"])

    assert harness["refreshed"] == [("practice_alpha", job.MAX_REFRESH_PER_TENANT)]


def test_a_practice_without_a_clearinghouse_opens_no_session(harness: dict[str, Any]) -> None:
    del harness["clients"]["gamma"]

    job.run([])

    assert "practice_gamma" not in harness["sessions"]
    assert [schema for schema, _ in harness["refreshed"]] == [
        "practice_alpha",
        "practice_beta",
        "practice_delta",
    ]


def test_one_practices_vendor_error_rolls_back_and_the_run_goes_on(
    harness: dict[str, Any],
) -> None:
    assert job.run([]) == 0

    sessions = harness["sessions"]
    assert sessions["practice_beta"].rolled_back
    assert not sessions["practice_beta"].committed
    assert sessions["practice_alpha"].committed
    assert sessions["practice_delta"].committed


def test_dry_run_calls_no_clearinghouse(
    harness: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(job, "_count_open", lambda _schema: 3)

    job.run(["--dry-run"])

    assert harness["refreshed"] == []


def test_refresh_tenant_refuses_a_schema_name_that_is_not_an_identifier() -> None:
    with pytest.raises(ValueError, match="schema"):
        job.refresh_tenant("practice; drop", object(), limit=1)  # type: ignore[arg-type]  # never reached
