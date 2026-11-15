# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Per-pool checkout counters and the periodic ``db_pool_stats`` log line.

Each engine this package builds uses a QueuePool subclass made by
:func:`timed_queue_pool`, which times every checkout (the wait for a free
connection plus, for a new one, the connect) and counts checkouts that gave up
at the pool timeout. :func:`log_pool_stats` writes one line per pool with the
counts since the previous line and the connections in use right now, so pool
sizes can be chosen from what a deployment actually does.

The timed checkout also includes the ``pool_pre_ping`` round trip, which every
engine here makes on every checkout. So ``wait_total_ms`` on a pool with many
checkouts is mostly ping time, not queueing. Read queueing from ``timeouts``
and from a ``wait_max_ms`` well above one round trip.

The ``timeouts`` count is the one to watch on the ``nested`` pool: it is small
and its checkout gives up after ``database_nested_pool_timeout_seconds``, so a
caller that breaks its leaf-only contract (see ``app.db.get_nested_engine``)
shows up there as timeouts rather than as a stalled process.

Counts and durations only: no statement, no parameter, no user.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exc
from sqlalchemy.pool import PoolProxiedConnection, QueuePool

logger = logging.getLogger(__name__)

#: ``event`` field on every pool stats line, the stable filter key.
POOL_STATS_EVENT = "db_pool_stats"


@dataclass
class _Counters:
    checkouts: int = 0
    timeouts: int = 0
    wait_total_s: float = 0.0
    wait_max_s: float = 0.0


class _PoolStats:
    """Counters for one named pool, shared by every pool built under that name.

    An engine's ``dispose()`` replaces its pool with a fresh instance of the
    same class; the counters and the reference to the live pool survive that.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = _Counters()
        self.pool: QueuePool | None = None

    def record(self, waited_s: float, *, timed_out: bool) -> None:
        with self._lock:
            c = self._counters
            if timed_out:
                c.timeouts += 1
            else:
                c.checkouts += 1
            c.wait_total_s += waited_s
            c.wait_max_s = max(c.wait_max_s, waited_s)

    def take(self) -> _Counters:
        with self._lock:
            taken, self._counters = self._counters, _Counters()
        return taken


_registry: dict[str, _PoolStats] = {}
_registry_lock = threading.Lock()


def _stats_for(name: str) -> _PoolStats:
    with _registry_lock:
        return _registry.setdefault(name, _PoolStats())


def timed_queue_pool(name: str) -> type[QueuePool]:
    """A QueuePool class whose checkouts are counted under *name*.

    Pass it as ``create_engine(poolclass=...)``. One name per engine role
    (``main``, ``nested``); building the engine again under the same name, as
    tests do, continues the same counters.
    """
    stats = _stats_for(name)

    class _TimedQueuePool(QueuePool):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            stats.pool = self

        def connect(self) -> PoolProxiedConnection:
            started = time.monotonic()
            try:
                conn = super().connect()
            except exc.TimeoutError:
                stats.record(time.monotonic() - started, timed_out=True)
                raise
            stats.record(time.monotonic() - started, timed_out=False)
            return conn

    _TimedQueuePool.__name__ = f"TimedQueuePool[{name}]"
    _TimedQueuePool.__qualname__ = _TimedQueuePool.__name__
    return _TimedQueuePool


def log_pool_stats() -> None:
    """Log one ``db_pool_stats`` line per pool that saw use, then reset its counters.

    A pool with no checkouts, no timeouts and nothing in use since the last line
    stays quiet, so an idle instance writes nothing.
    """
    with _registry_lock:
        named = list(_registry.items())
    for name, stats in named:
        pool = stats.pool
        counters = stats.take()
        in_use = pool.checkedout() if pool is not None else 0
        if not (counters.checkouts or counters.timeouts or in_use):
            continue
        size = pool.size() if pool is not None else 0
        overflow = pool.overflow() if pool is not None else 0
        logger.info(
            "db_pool_stats pool=%s checkouts=%d timeouts=%d in_use=%d size=%d "
            "overflow=%d wait_max_ms=%d wait_total_ms=%d",
            name,
            counters.checkouts,
            counters.timeouts,
            in_use,
            size,
            overflow,
            round(counters.wait_max_s * 1000),
            round(counters.wait_total_s * 1000),
            extra={
                "event": POOL_STATS_EVENT,
                "pool": name,
                "checkouts": counters.checkouts,
                "timeouts": counters.timeouts,
                "in_use": in_use,
                "pool_size": size,
                "overflow": overflow,
                "wait_max_ms": round(counters.wait_max_s * 1000),
                "wait_total_ms": round(counters.wait_total_s * 1000),
            },
        )


async def pool_stats_loop(interval_s: float) -> None:
    """Call :func:`log_pool_stats` every *interval_s* seconds until cancelled.

    It reads in-process counters only and never touches the database, so it
    runs on the event loop itself.
    """
    while True:
        await asyncio.sleep(interval_s)
        try:
            log_pool_stats()
        except Exception:
            logger.exception("db_pool_stats failed")
