# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``db_pool_stats`` line: checkouts, timeouts, in-use and wait, per pool, counts only."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from app.db.pool_stats import POOL_STATS_EVENT, log_pool_stats, timed_queue_pool
from sqlalchemy import exc


def _lines(caplog: pytest.LogCaptureFixture, pool: str) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if getattr(r, "event", None) == POOL_STATS_EVENT and getattr(r, "pool", None) == pool
    ]


def test_checkouts_in_use_and_timeouts_are_counted_then_reset(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = timed_queue_pool("unit-a")(MagicMock, pool_size=1, max_overflow=0, timeout=0.05)
    log_pool_stats()  # start from zero
    held = pool.connect()
    with pytest.raises(exc.TimeoutError):
        pool.connect()

    with caplog.at_level(logging.INFO, logger="app.db.pool_stats"):
        log_pool_stats()
    (line,) = _lines(caplog, "unit-a")
    assert (line.checkouts, line.timeouts, line.in_use, line.pool_size) == (1, 1, 1, 1)  # type: ignore[attr-defined]
    assert line.wait_max_ms >= 50  # type: ignore[attr-defined]  # the timed-out wait

    held.close()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.db.pool_stats"):
        log_pool_stats()
    assert _lines(caplog, "unit-a") == []  # idle since the last line: quiet


def test_a_disposed_pool_keeps_its_counters_under_the_same_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = timed_queue_pool("unit-b")(MagicMock, pool_size=1, max_overflow=0)
    log_pool_stats()
    replacement = pool.recreate()  # what Engine.dispose() does
    held = replacement.connect()
    with caplog.at_level(logging.INFO, logger="app.db.pool_stats"):
        log_pool_stats()
    (line,) = _lines(caplog, "unit-b")
    assert (line.checkouts, line.in_use) == (1, 1)  # type: ignore[attr-defined]
    held.close()
