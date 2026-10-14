# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""/api/health while the database is unreachable.

A new instance's first database connections can take a while to come up. A
platform health probe reading the endpoint during that window must see 503,
the answer that means "not ready yet", rather than the unhandled 500 an
OperationalError used to produce.
"""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def unreachable_engine() -> Iterator[Engine]:
    """A real engine pointed at a port nothing listens on.

    Connecting raises the driver's own OperationalError, so the handler is
    exercised against the exception a down database actually produces.
    """
    engine = create_engine(
        "postgresql+psycopg2://pablo:unused@127.0.0.1:1/pablo",
        connect_args={"connect_timeout": 2},
    )
    yield engine
    engine.dispose()


def test_database_unreachable_answers_503_unavailable(
    client: TestClient, unreachable_engine: Engine
) -> None:
    with patch("app.main.get_engine", return_value=unreachable_engine):
        response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_healthy_body_is_unchanged(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "server_version", "git_sha", "min_client_versions"}
    assert body["status"] == "healthy"
