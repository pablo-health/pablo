# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test for the release valve behind the autouse fixture in
``conftest.py``.

fastapi caches the classification of every dependency callable
(generator? async generator? coroutine?) in module-level ``lru_cache``
helpers under ``fastapi.dependencies.models``, keyed on the callable
itself. This suite builds a fresh app per test, each with its own
generator session dependency closing over an engine, so those caches
accumulate a strong reference to every engine ever built unless they're
cleared between app constructions.

This test proves the mechanism directly: build a batch of apps whose
dependency closes over a sentinel object, confirm the objects are still
reachable (the leak is real), then call the same cache-clearing helper
the autouse fixture calls and confirm every object becomes unreachable.
"""

from __future__ import annotations

import gc
import weakref
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI

from tests_integration.conftest import clear_fastapi_dependency_caches

if TYPE_CHECKING:
    from collections.abc import Callable

APP_COUNT = 50


def _make_session_dependency(sentinel: object) -> Callable[[], Any]:
    """A generator dependency closing over ``sentinel``, mirroring a real
    per-app DB session dependency closing over its engine."""

    def get_session() -> Any:
        yield sentinel

    return get_session


def _build_apps_with_closures() -> list[weakref.ReferenceType[object]]:
    refs: list[weakref.ReferenceType[object]] = []
    for _ in range(APP_COUNT):

        class _Sentinel:
            pass

        sentinel = _Sentinel()
        refs.append(weakref.ref(sentinel))

        app = FastAPI()
        dependency = _make_session_dependency(sentinel)

        @app.get("/probe")
        def route(value: Any = Depends(dependency)) -> dict[str, bool]:
            return {"ok": True}

    return refs


def test_fastapi_dependency_cache_clear_releases_closures() -> None:
    refs = _build_apps_with_closures()
    gc.collect()

    reachable_before = sum(1 for ref in refs if ref() is not None)
    assert reachable_before == APP_COUNT, (
        "expected every sentinel to still be reachable before clearing "
        "fastapi's dependency-classification caches — if this fails, "
        "the installed fastapi version no longer exhibits the leak and "
        "the version cap this test guards against may be obsolete"
    )

    clear_fastapi_dependency_caches()
    gc.collect()

    reachable_after = sum(1 for ref in refs if ref() is not None)
    assert reachable_after == 0, (
        "sentinels are still reachable after clear_fastapi_dependency_caches() "
        "— the release valve the autouse fixture relies on is broken"
    )
