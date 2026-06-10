# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end launch-intent store against real PostgreSQL.

Proves the Postgres-backed store (the durable, multi-instance path)
round-trips through ``platform.launch_intents``: only the SHA-256 hash
is persisted, redemption is single-use and atomic, and expired rows are
rejected. The unit suite (``tests/test_routes_launch.py``) covers the
in-memory store and the route wiring.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase
from app.services.launch_intent_store import PostgresLaunchIntentStore
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        conn.execute(
            text(f"SET search_path = {DEFAULT_PRACTICE_SCHEMA}, {PLATFORM_SCHEMA}, public")
        )
        PlatformBase.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("TRUNCATE TABLE platform.launch_intents"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _user_id() -> str:
    return str(uuid.uuid4())


def test_create_persists_only_the_hash(pg_session: Session) -> None:
    store = PostgresLaunchIntentStore(pg_session)
    user_id = _user_id()
    intent_id = store.create(user_id=user_id, appointment_id="appt-1")

    # 128-bit token → 22 url-safe chars.
    assert len(intent_id) == 22

    rows = pg_session.execute(
        text(
            "SELECT intent_hash, user_id, appointment_id, consumed_at "
            "FROM platform.launch_intents"
        )
    ).all()
    assert len(rows) == 1
    stored_hash, stored_user, stored_appt, consumed = rows[0]
    # The raw id is never stored — only its SHA-256 hash.
    assert stored_hash == hashlib.sha256(intent_id.encode()).hexdigest()
    assert stored_hash != intent_id
    assert str(stored_user) == user_id
    assert stored_appt == "appt-1"
    assert consumed is None


def test_redeem_happy_path_returns_binding(pg_session: Session) -> None:
    store = PostgresLaunchIntentStore(pg_session)
    user_id = _user_id()
    intent_id = store.create(user_id=user_id, appointment_id="appt-9")
    redeemed = store.redeem(intent_id)
    assert redeemed is not None
    assert redeemed.user_id == user_id
    assert redeemed.appointment_id == "appt-9"


def test_redeem_is_single_use_atomic(pg_session: Session) -> None:
    store = PostgresLaunchIntentStore(pg_session)
    intent_id = store.create(user_id=_user_id(), appointment_id="appt-x")
    first = store.redeem(intent_id)
    assert first is not None
    # Second redeem of the same id finds it consumed → None.
    assert store.redeem(intent_id) is None


def test_redeem_unknown_id_returns_none(pg_session: Session) -> None:
    store = PostgresLaunchIntentStore(pg_session)
    assert store.redeem("never-issued") is None


def test_redeem_expired_returns_none(pg_session: Session) -> None:
    # ttl=0 → the row is written already-expired; redeem must reject it.
    store = PostgresLaunchIntentStore(pg_session, ttl_seconds=0)
    intent_id = store.create(user_id=_user_id(), appointment_id="appt-exp")
    assert store.redeem(intent_id) is None
