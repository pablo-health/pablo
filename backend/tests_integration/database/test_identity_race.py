# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test: ``PostgresIdentityRepository.resolve_or_create`` is
race-safe under parallel first-login requests for the same firebase_uid.

A user's first authenticated session fires multiple parallel API calls
(useAuth, useStatus, useBaaStatus, …) before any ``user_identities``
row exists. The pre-fix SELECT-then-INSERT path raced — one request
won the INSERT, the rest crashed with::

    sqlalchemy.exc.IntegrityError: UniqueViolation
      duplicate key value violates unique constraint "user_identities_pkey"

This test launches N threads that all call ``resolve_or_create`` for the
same (provider, subject_id) and asserts (a) none raise, (b) every caller
gets the same canonical user_id, and (c) the table holds exactly one row.

Threads each use their own ``Session`` because SQLAlchemy ``Session`` is
not thread-safe and a shared session can't reproduce the cross-connection
race that hits production.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import pytest
from app.db import PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase, UserIdentityRow
from app.repositories.postgres.identity import PostgresIdentityRepository
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url,
    reason="PostgreSQL not configured. Set DATABASE_URL.",
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        PlatformBase.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def clean_user_identities(engine: Engine) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {PLATFORM_SCHEMA}.user_identities"))
    yield
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {PLATFORM_SCHEMA}.user_identities"))


def test_resolve_or_create_is_race_safe_for_concurrent_first_login(
    engine: Engine, clean_user_identities: None
) -> None:
    """N threads racing on the same firebase_uid all succeed with the same id."""
    n_threads = 10
    provider = "firebase"
    subject_id = "race-test-firebase-uid-1"
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []
    resolved_ids: list[str] = []
    lock = threading.Lock()

    def resolve() -> None:
        session = factory()
        try:
            barrier.wait()
            repo = PostgresIdentityRepository(session)
            user_id = repo.resolve_or_create(provider, subject_id)
            session.commit()
            with lock:
                resolved_ids.append(user_id)
        except BaseException as exc:
            with lock:
                errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=resolve) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"resolve_or_create raised under concurrency: {errors!r}"
    assert len(resolved_ids) == n_threads
    assert len(set(resolved_ids)) == 1, (
        f"concurrent callers diverged on user_id: {set(resolved_ids)!r}"
    )

    with factory() as verify:
        rows = verify.execute(
            select(UserIdentityRow).where(
                UserIdentityRow.provider == provider,
                UserIdentityRow.subject_id == subject_id,
            )
        ).all()
    assert len(rows) == 1, f"expected exactly one user_identities row, got {len(rows)}"


def test_resolve_or_create_is_idempotent_for_repeat_calls(
    engine: Engine, clean_user_identities: None
) -> None:
    """Sequential calls for the same identity must return the same user_id."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    provider = "firebase"
    subject_id = "repeat-call-firebase-uid"

    with factory() as session:
        repo = PostgresIdentityRepository(session)
        first = repo.resolve_or_create(provider, subject_id)
        session.commit()

    with factory() as session:
        repo = PostgresIdentityRepository(session)
        second = repo.resolve_or_create(provider, subject_id)
        session.commit()

    assert first == second
