# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""DatabaseSessionMiddleware while the database cannot be reached yet.

The middleware's prelude arms ``search_path`` with SQL before any route runs,
so it is the first thing on a request to open a connection. On a fresh
instance whose first connections are still coming up, that connect failed and
the OperationalError escaped as an unhandled exception — the platform's startup
probe on ``/api/health`` logged a crash on every deploy, even though the health
handler itself already answers 503 for exactly this condition.

These tests drive the real app against a real engine pointed at a port nothing
listens on, so the prelude meets the driver's own OperationalError, and pin:

* ``/api/health`` answers 503 ``{"status": "unavailable"}`` with nothing logged
  at ERROR;
* an authenticated route answers 503 with ``Retry-After``, not 500;
* an OperationalError raised by a route handler is NOT absorbed — only the
  prelude is covered.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from app.db.middleware import DATABASE_UNAVAILABLE_RETRY_AFTER_S
from app.main import app
from app.routes.patients import get_patient_repository
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def unreachable_engine() -> Iterator[Engine]:
    """A real engine pointed at a port nothing listens on.

    Connecting raises the driver's own OperationalError, the same exception a
    database that is not reachable yet produces.
    """
    engine = create_engine(
        "postgresql+psycopg2://pablo:unused@127.0.0.1:1/pablo",
        connect_args={"connect_timeout": 2},
    )
    yield engine
    engine.dispose()


@pytest.fixture
def database_unreachable(unreachable_engine: Engine) -> Iterator[None]:
    """Route both the middleware's session and the health check at the dead engine."""
    factory = sessionmaker(bind=unreachable_engine, expire_on_commit=False)
    with (
        patch("app.db.middleware.get_session_factory", return_value=factory),
        patch("app.main.get_engine", return_value=unreachable_engine),
    ):
        yield


def _error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.usefixtures("database_unreachable")
def test_health_answers_503_unavailable_without_logging_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    response = TestClient(app).get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["Retry-After"] == str(DATABASE_UNAVAILABLE_RETRY_AFTER_S)
    assert _error_records(caplog) == []
    warnings = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "database_unavailable_in_request_prelude"
    ]
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING


@pytest.mark.usefixtures("database_unreachable")
def test_authenticated_route_answers_503_with_retry_after(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)

    response = TestClient(app).get(
        "/api/patients", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == str(DATABASE_UNAVAILABLE_RETRY_AFTER_S)
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert _error_records(caplog) == []


def test_pool_timeout_in_the_prelude_answers_503() -> None:
    """No pooled connection freeing up in time is the same "not yet" as no server."""
    with patch(
        "app.db.middleware.set_tenant_schema",
        side_effect=PoolTimeoutError("QueuePool limit reached"),
    ):
        response = TestClient(app).get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_operational_error_raised_by_a_route_still_propagates(client: TestClient) -> None:
    """The prelude succeeds; the handler's dependency fails mid-request.

    That is a real failure inside the request, not a database that is not up
    yet, and it must keep surfacing as one rather than being reshaped into a
    retryable 503.
    """

    def _repository_that_fails() -> Any:
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    # ``client`` stands in for a signed-in clinician; only the repository
    # behind the route is swapped for one that fails. The fixture clears the
    # overrides afterwards.
    app.dependency_overrides[get_patient_repository] = _repository_that_fails

    with pytest.raises(OperationalError):
        client.get("/api/patients")
