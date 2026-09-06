# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An in-memory SQLite engine for tests that drive ORM rows without Postgres.

``app.db`` registers a class-level ``checkin`` listener on ``Engine`` that
issues a Postgres ``SET search_path`` — SQLite cannot parse it. The listener
is detached for the life of the engine and put back afterwards.

The package is importable under two names in this repository's test runs
(``app`` from ``backend/`` and ``backend.app`` from the repository root),
and each import registers its own copy of the listener, so both copies are
detached. Which one is loaded depends on what ran earlier in the session;
handling both keeps these fixtures independent of test order.
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

_LISTENER = "_reset_search_path_on_checkin"


def _checkin_listeners() -> list[Any]:
    listeners = []
    for module_name in ("app.db", "backend.app.db"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        listener = getattr(module, _LISTENER, None)
        if listener is not None and listener not in listeners:
            listeners.append(listener)
    return listeners


@contextmanager
def sqlite_engine(tables: Iterable[Any]) -> Iterator[Engine]:
    """An in-memory SQLite engine with ``tables`` created.

    One connection shared across threads, so a FastAPI test client's worker
    threads see the rows a test seeded.
    """
    detached: list[tuple[Any, int]] = []
    for listener in _checkin_listeners():
        count = 0
        while event.contains(Engine, "checkin", listener):
            event.remove(Engine, "checkin", listener)
            count += 1
        detached.append((listener, count))

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    for table in tables:
        table.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        for listener, count in detached:
            for _ in range(count):
                event.listen(Engine, "checkin", listener)
