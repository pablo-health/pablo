# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Read actions registered with their own coalescing window.

A screen that lists who needs attention today records one read per person it
names, and it refreshes on every visit. Without coalescing, a clinician who
opens it ten times in a morning records the same disclosure ten times. A
deployment registers such an action with a window (``register_coalesced_read``);
the built-in reads and ``audit_read_coalesce_seconds`` are untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING

import pytest
from app import utcnow
from app.models import User
from app.models.audit import AuditAction, ResourceType
from app.repositories.audit import InMemoryAuditRepository
from app.services import audit_service as audit_module
from app.services.audit_service import AuditService, CoalesceWindow, register_coalesced_read
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_LIST_READ = "daily_list_viewed"


class _Pipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, int | None]] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> None:
        self._queued.append((key, ex))

    def execute(self) -> list[bool | None]:
        return [self._redis.set(key, "1", nx=True, ex=ex) for key, ex in self._queued]


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int | None] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = ex
        return True

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)


class _BoomRedis(_FakeRedis):
    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        raise RuntimeError("simulated Redis outage")

    def pipeline(self) -> _Pipeline:
        raise RuntimeError("simulated Redis outage")


class _FixedDay:
    """Stands in for ``datetime`` in ``app.utcnow`` so entries carry a chosen time."""

    current = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:
        return cls.current if tz is None else cls.current.astimezone(tz)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    get_settings.cache_clear()
    monkeypatch.delenv("AUDIT_READ_COALESCE_SECONDS", raising=False)
    monkeypatch.setattr(utcnow, "datetime", _FixedDay)
    _FixedDay.current = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)
    fake = _FakeRedis()
    monkeypatch.setattr("app.redis_client.get_redis_client", lambda: fake)
    saved = dict(audit_module._REGISTERED_WINDOWS)
    yield fake
    audit_module._REGISTERED_WINDOWS.clear()
    audit_module._REGISTERED_WINDOWS.update(saved)
    get_settings.cache_clear()


@pytest.fixture
def repo() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@pytest.fixture
def audit(repo: InMemoryAuditRepository) -> AuditService:
    return AuditService(repo)


@pytest.fixture
def user() -> User:
    return User(
        id="user-123",
        email="clinician@example.com",
        name="Test Clinician",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _load(audit: AuditService, user: User, patient_ids: list[str]) -> None:
    """One refresh of the list: one read per person it names, in one batch."""
    with audit.batched():
        for patient_id in patient_ids:
            audit.log(_LIST_READ, user, None, ResourceType.PATIENT, patient_id)


def _rows(repo: InMemoryAuditRepository) -> list[str]:
    return [e.resource_id for e in repo._entries if e.action == _LIST_READ]


def test_a_second_load_the_same_day_writes_nothing_for_the_same_people(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))

    _load(audit, user, ["p1", "p2"])
    _FixedDay.current = datetime(2026, 10, 18, 16, 30, tzinfo=UTC)
    _load(audit, user, ["p1", "p2"])

    assert sorted(_rows(repo)) == ["p1", "p2"]


def test_someone_named_later_in_the_day_is_recorded_then(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))

    _load(audit, user, ["p1"])
    _load(audit, user, ["p1", "p3"])

    assert sorted(_rows(repo)) == ["p1", "p3"]


def test_a_new_day_records_again(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))

    _load(audit, user, ["p1"])
    _FixedDay.current = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    _load(audit, user, ["p1"])

    assert _rows(repo) == ["p1", "p1"]


def test_the_single_call_path_coalesces_the_same_way(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))

    for _ in range(3):
        audit.log(_LIST_READ, user, None, ResourceType.PATIENT, "p1")

    assert _rows(repo) == ["p1"]


def test_a_seconds_window_is_honoured_with_its_own_expiry(
    audit: AuditService, repo: InMemoryAuditRepository, user: User, fake_redis: _FakeRedis
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(seconds=900))

    _load(audit, user, ["p1"])
    _load(audit, user, ["p1"])

    assert _rows(repo) == ["p1"]
    assert list(fake_redis.store.values()) == [900]


def test_registering_one_action_leaves_built_in_reads_alone(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    # audit_read_coalesce_seconds is unset (0): built-in reads keep one row each.
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))

    for _ in range(2):
        audit.log(AuditAction.PATIENT_VIEWED, user, None, ResourceType.PATIENT, "p1")

    assert sum(1 for e in repo._entries if e.action == AuditAction.PATIENT_VIEWED.value) == 2


def test_an_unregistered_action_is_never_gated(
    audit: AuditService, repo: InMemoryAuditRepository, user: User
) -> None:
    _load(audit, user, ["p1"])
    _load(audit, user, ["p1"])

    assert _rows(repo) == ["p1", "p1"]


def test_a_redis_outage_writes_every_row(
    audit: AuditService,
    repo: InMemoryAuditRepository,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_coalesced_read(_LIST_READ, CoalesceWindow(calendar_day=True))
    monkeypatch.setattr("app.redis_client.get_redis_client", _BoomRedis)

    _load(audit, user, ["p1"])
    audit.log(_LIST_READ, user, None, ResourceType.PATIENT, "p1")

    assert _rows(repo) == ["p1", "p1"]


def test_a_window_is_seconds_or_a_day_never_both_or_neither() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CoalesceWindow()
    with pytest.raises(ValueError, match="exactly one"):
        CoalesceWindow(seconds=60, calendar_day=True)


def test_a_built_in_coalesced_read_cannot_be_registered_again() -> None:
    with pytest.raises(ValueError, match="already coalesced"):
        register_coalesced_read(AuditAction.PATIENT_VIEWED, CoalesceWindow(calendar_day=True))
