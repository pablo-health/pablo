# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test: ensure_schemas survives concurrent Cloud Run boots.

Multiple Cloud Run instances can start within the same second during a
deployment rollout overlapping with a min-instance warm-up. Every instance
calls ``ensure_schemas`` at import time, and ``create_all``'s
check-then-create dance is not atomic — without serialization, two
instances both see "table missing" and both emit ``CREATE TABLE``, the
loser hits ``DuplicateTable`` and exits, failing the deploy.

This test reproduces the race by launching N threads that all call
``ensure_schemas`` simultaneously and asserts none raise. Skipped unless
real Postgres is configured (same convention as the rest of this
directory).
"""

from __future__ import annotations

import os
import threading

import pytest
from app.db import get_engine
from app.db.provisioning import ensure_schemas

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres. "
        "Start proxy with: make db-dev-proxy"
    ),
)


def test_ensure_schemas_concurrent_boots_do_not_race() -> None:
    """Two threads calling ensure_schemas at the same time must both succeed.

    Without the advisory-lock fix, this fails with ``DuplicateTable``.
    """
    engine = get_engine()
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def boot() -> None:
        try:
            barrier.wait()
            ensure_schemas(engine)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=boot) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"ensure_schemas raised under concurrency: {errors!r}"
