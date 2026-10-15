# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""``AuditService.batched``: a list view's rows in one trip each.

The rows are the same rows the per-call path writes. What changes is the
trip count: one Redis pipeline for the coalesce gate, one repository
write, one mirror write.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.models import Patient, User
from app.models.audit import AuditAction, AuditLogEntry
from app.repositories.audit import InMemoryAuditRepository
from app.services import audit_cloud_logging
from app.services.audit_service import AuditService
from app.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator


class _CountingRepository(InMemoryAuditRepository):
    """Counts write calls so a test can tell one bulk write from N appends."""

    def __init__(self) -> None:
        super().__init__()
        self.append_calls = 0
        self.append_many_calls = 0

    def append(self, entry: AuditLogEntry) -> None:
        self.append_calls += 1
        super().append(entry)

    def append_many(self, entries: list[AuditLogEntry]) -> None:
        self.append_many_calls += 1
        self._entries.extend(entries)


class _Pipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._queued: list[tuple[str, int | None]] = []

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> None:
        self._queued.append((key, ex))

    def execute(self) -> list[bool | None]:
        self._redis.executes += 1
        return [self._redis.set(key, "1", nx=True, ex=ex) for key, ex in self._queued]


class _FakeRedis:
    """``SET NX EX`` plus a pipeline that replays its queue on ``execute``."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.executes = 0
        self.single_sets = 0

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def pipeline(self) -> _Pipeline:
        return _Pipeline(self)


class _BoomPipelineRedis(_FakeRedis):
    def pipeline(self) -> _Pipeline:
        raise RuntimeError("simulated Redis outage")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def repo() -> _CountingRepository:
    return _CountingRepository()


@pytest.fixture
def audit(repo: _CountingRepository) -> AuditService:
    return AuditService(repo)


@pytest.fixture
def user() -> User:
    return User(
        id="user-123",
        email="test@example.com",
        name="Test User",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


@pytest.fixture
def request_() -> MagicMock:
    request = MagicMock()
    request.headers = {"User-Agent": "TestBrowser/1.0"}
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    return request


def _patient(patient_id: str) -> Patient:
    return Patient(
        id=patient_id,
        first_name="Test",
        last_name="Patient",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


def _view_page(audit: AuditService, user: User, request: MagicMock, ids: list[str]) -> None:
    with audit.batched():
        for pid in ids:
            audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request, _patient(pid))


def test_a_page_is_one_write_with_one_row_per_record(
    audit: AuditService, repo: _CountingRepository, user: User, request_: MagicMock
) -> None:
    _view_page(audit, user, request_, ["p1", "p2", "p3"])

    assert [e.resource_id for e in repo._entries] == ["p1", "p2", "p3"]
    assert repo.append_many_calls == 1
    assert repo.append_calls == 0


def test_nothing_is_written_until_the_block_ends(
    audit: AuditService, repo: _CountingRepository, user: User, request_: MagicMock
) -> None:
    with audit.batched():
        audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p1"))
        assert repo._entries == []
    assert len(repo._entries) == 1


def test_the_coalesce_gate_is_one_round_trip_and_still_collapses(
    audit: AuditService,
    repo: _CountingRepository,
    user: User,
    request_: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setenv("AUDIT_READ_COALESCE_SECONDS", "900")
    monkeypatch.setattr("app.redis_client.get_redis_client", lambda: fake)

    # The same record twice in one page, and a page already seen this window.
    _view_page(audit, user, request_, ["p1", "p2", "p1"])
    _view_page(audit, user, request_, ["p2", "p3"])

    assert [e.resource_id for e in repo._entries] == ["p1", "p2", "p3"]
    assert fake.executes == 2, "one pipeline per page, not one SET per row"


def test_a_redis_outage_writes_every_row(
    audit: AuditService,
    repo: _CountingRepository,
    user: User,
    request_: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boom = _BoomPipelineRedis()
    monkeypatch.setenv("AUDIT_READ_COALESCE_SECONDS", "900")
    monkeypatch.setattr("app.redis_client.get_redis_client", lambda: boom)

    _view_page(audit, user, request_, ["p1", "p1"])

    assert [e.resource_id for e in repo._entries] == ["p1", "p1"]


def test_a_write_that_is_not_a_coalesced_read_is_never_gated(
    audit: AuditService,
    repo: _CountingRepository,
    user: User,
    request_: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeRedis()
    monkeypatch.setenv("AUDIT_READ_COALESCE_SECONDS", "900")
    monkeypatch.setattr("app.redis_client.get_redis_client", lambda: fake)

    with audit.batched():
        for _ in range(2):
            audit.log_patient_action(AuditAction.PATIENT_UPDATED, user, request_, _patient("p1"))

    assert len(repo._entries) == 2
    assert fake.executes == 0


def test_nested_blocks_join_the_outer_one(
    audit: AuditService, repo: _CountingRepository, user: User, request_: MagicMock
) -> None:
    with audit.batched():
        audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p1"))
        with audit.batched():
            audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p2"))
        assert repo._entries == [], "the inner block does not flush the outer one"
    assert len(repo._entries) == 2
    assert repo.append_many_calls == 1


def test_a_block_that_raises_writes_nothing_and_resets(
    audit: AuditService, repo: _CountingRepository, user: User, request_: MagicMock
) -> None:
    def _page_that_fails() -> None:
        with audit.batched():
            audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p1"))
            raise RuntimeError("the response was never sent")

    with pytest.raises(RuntimeError):
        _page_that_fails()

    assert repo._entries == []
    # The service is usable again, on the per-call path.
    audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p2"))
    assert repo.append_calls == 1


def test_a_failed_bulk_write_fails_the_request(user: User, request_: MagicMock) -> None:
    class _Broken(InMemoryAuditRepository):
        def append_many(self, entries: list[AuditLogEntry]) -> None:
            raise RuntimeError("database gone")

    audit = AuditService(_Broken())
    with pytest.raises(RuntimeError, match="database gone"):
        _view_page(audit, user, request_, ["p1"])


def test_the_mirror_gets_the_page_in_one_call(
    audit: AuditService, user: User, request_: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUDIT_DUAL_WRITE_ENABLED", "true")
    batches: list[list[str]] = []
    singles: list[str] = []
    monkeypatch.setattr(
        audit_cloud_logging,
        "write_many_to_cloud_logging",
        lambda entries: batches.append([e.resource_id for e in entries]),
    )
    monkeypatch.setattr(
        audit_cloud_logging, "write_to_cloud_logging", lambda e: singles.append(e.resource_id)
    )

    _view_page(audit, user, request_, ["p1", "p2"])
    audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, request_, _patient("p3"))

    assert batches == [["p1", "p2"]]
    assert singles == ["p3"]


def test_the_mirror_failing_does_not_fail_the_request(
    audit: AuditService,
    repo: _CountingRepository,
    user: User,
    request_: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDIT_DUAL_WRITE_ENABLED", "true")

    def _boom(entries: list[AuditLogEntry]) -> None:
        raise RuntimeError("mirror down")

    monkeypatch.setattr(audit_cloud_logging, "write_many_to_cloud_logging", _boom)

    _view_page(audit, user, request_, ["p1", "p2"])

    assert len(repo._entries) == 2
