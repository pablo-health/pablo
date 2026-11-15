# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An in-memory Redis with the operations ``practice_resolution_cache`` uses.

Strings and sets, with TTLs recorded rather than enforced: a test that needs
an entry to expire deletes it, and a test can read what TTL a key was given.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.ttls: dict[str, int] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            msg = "redis down"
            raise ConnectionError(msg)

    def get(self, key: str) -> str | None:
        self._check()
        return self.strings.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._check()
        self.strings[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def sadd(self, key: str, *members: str) -> int:
        self._check()
        self.sets.setdefault(key, set()).update(members)
        return len(members)

    def smembers(self, key: str) -> set[str]:
        self._check()
        return set(self.sets.get(key, set()))

    def expire(self, key: str, seconds: int) -> bool:
        self._check()
        self.ttls[key] = seconds
        return True

    def delete(self, *keys: str) -> int:
        self._check()
        removed = 0
        for key in keys:
            removed += int(self.strings.pop(key, None) is not None)
            removed += int(self.sets.pop(key, None) is not None)
            self.ttls.pop(key, None)
        return removed

    def scan_iter(self, match: str = "*") -> Iterator[str]:
        self._check()
        yield from [k for k in (*self.strings, *self.sets) if fnmatch.fnmatchcase(k, match)]

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)

    def keys_with_prefix(self, prefix: str) -> list[str]:
        return sorted(k for k in (*self.strings, *self.sets) if k.startswith(prefix))


class _Pipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def queue(*args: Any, **kwargs: Any) -> _Pipeline:
            self._calls.append((name, args, kwargs))
            return self

        return queue

    def execute(self) -> list[Any]:
        self._redis._check()
        return [getattr(self._redis, n)(*a, **kw) for n, a, kw in self._calls]
