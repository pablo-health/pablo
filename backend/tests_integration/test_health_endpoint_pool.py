# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""/api/health against a real database whose pool has no connection to spare.

When every pooled connection is checked out and none frees up in time,
SQLAlchemy raises its pool TimeoutError rather than a driver error. The
health check reports that as 503 "unavailable" too, and returns to 200 with
its usual body once a connection is free again.
"""

import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine


@pytest.fixture
def one_connection_engine() -> Iterator[Engine]:
    engine = create_engine(
        os.environ["DATABASE_URL"], pool_size=1, max_overflow=0, pool_timeout=0.5
    )
    yield engine
    engine.dispose()


def test_exhausted_pool_answers_503_then_recovers(one_connection_engine: Engine) -> None:
    client = TestClient(app)
    with patch("app.main.get_engine", return_value=one_connection_engine):
        held = one_connection_engine.connect()
        try:
            response = client.get("/api/health")
            assert response.status_code == 503
            assert response.json() == {"status": "unavailable"}
        finally:
            held.close()

        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
