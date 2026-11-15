# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The session-liveness poll touches no database, and practice resolution is cached.

Every open tab polls ``GET /api/auth/session`` (and ``POST
/api/auth/session/touch`` on activity). The answer comes from the idle-session
store and the verified token, so the request middleware opens no session for
those two paths and the claims dependency looks nothing up. Every other
signed-in request still resolves its practice from the token's email; that
resolution is repeated from Redis for a short time and dropped when a practice
or mapping row commits a change.

Statements are counted with a ``before_cursor_execute`` listener on every
engine, around the call under test only. Each zero is paired with a case on
the same harness that counts more than zero, so a counter that sees nothing
cannot pass them.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.engine import Engine
from tests.practice_resolution_fakes import FakeRedis

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_CACHE = "app.db.practice_resolution_cache"
_EMAIL_PREFIX = "practice-resolution:v1:email:"


class _Statements:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, _conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        self.seen.append(statement)

    @property
    def count(self) -> int:
        return len(self.seen)


@contextmanager
def _counting() -> Iterator[_Statements]:
    """Count every SQL statement any engine sends while the block runs."""
    statements = _Statements()
    event.listen(Engine, "before_cursor_execute", statements)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", statements)


@pytest.fixture
def practice(engine: Engine) -> Iterator[SimpleNamespace]:
    """A provisioned practice with its owner's email mapped."""
    from app.db.platform_models import EmailTenantMappingRow, PracticeRow  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    label = uuid.uuid4().hex[:8]
    schema = f"practice_test_liveness_{label}"
    create_practice_schema(engine, schema)
    practice_id = f"liveness-{label}"
    email = f"owner-{label}@example.invalid"
    with Session(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Liveness",
                schema_name=schema,
                owner_email=email,
                created_at=utc_now(),
            )
        )
        session.flush()
        session.add(
            EmailTenantMappingRow(
                email=email, tenant_id=practice_id, practice_id=practice_id, created_at=utc_now()
            )
        )
        session.commit()
    try:
        yield SimpleNamespace(id=practice_id, schema=schema, email=email)
    finally:
        with Session(engine) as session:
            session.query(EmailTenantMappingRow).filter_by(practice_id=practice_id).delete()
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.commit()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture
def redis() -> Iterator[FakeRedis]:
    fake = FakeRedis()
    with patch(f"{_CACHE}.get_redis_client", return_value=fake):
        yield fake


@pytest.fixture
def no_redis() -> Iterator[None]:
    with patch(f"{_CACHE}.get_redis_client", return_value=None):
        yield


@contextmanager
def _signed_in(app: FastAPI, email: str) -> Iterator[Any]:
    """A client whose every request carries a verified token for ``email``."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    uid = f"fb{uuid.uuid4().hex[:26]}"
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=uid,
        email=email,
        email_verified=True,
        mfa_satisfied=True,
        claims={
            "uid": uid,
            "sub": uid,
            "email": email,
            "email_verified": True,
            "auth_time": 1_700_000_000,
            "firebase": {"sign_in_provider": "password"},
        },
    )
    with (
        patch("app.auth.service.verify_token", return_value=identity),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.headers["Authorization"] = "Bearer test-token"
        yield client


def _resolve(email: str) -> tuple[str, str] | None:
    from app.auth.service import _resolve_practice_from_email  # noqa: PLC0415

    return _resolve_practice_from_email(email)


# ---------------------------------------------------------------------------
# The liveness endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/auth/session"), ("POST", "/api/auth/session/touch")]
)
def test_session_liveness_runs_no_sql(
    fastapi_app: FastAPI, practice: SimpleNamespace, no_redis: None, method: str, path: str
) -> None:
    with _signed_in(fastapi_app, practice.email) as client, _counting() as statements:
        response = client.request(method, path)

    assert response.status_code == 200, response.text
    assert statements.seen == []


def test_a_signed_in_route_on_the_same_harness_does_run_sql(
    fastapi_app: FastAPI, practice: SimpleNamespace, no_redis: None
) -> None:
    """The counter sees the middleware's practice lookup on any other route."""
    with _signed_in(fastapi_app, practice.email) as client, _counting() as statements:
        client.get("/api/users/me/status")

    assert statements.count > 0


def test_a_device_bound_liveness_poll_keeps_its_session(
    fastapi_app: FastAPI, practice: SimpleNamespace, no_redis: None
) -> None:
    """A request with an install id keeps the session the device-proof check reads.

    The device-proof middleware looks the caller and the device up in the
    request's session. Without one it would raise; with it, an unenrolled
    install is refused by that check with its own code.
    """
    with (
        patch("app.main.settings.enable_dpop_validation", True),
        _signed_in(fastapi_app, practice.email) as client,
        _counting() as statements,
    ):
        response = client.get("/api/auth/session", headers={"X-Install-ID": "install-1"})

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "INVALID_DPOP_PROOF"
    assert statements.count > 0


# ---------------------------------------------------------------------------
# The practice-resolution cache
# ---------------------------------------------------------------------------


def test_a_repeat_resolution_is_served_without_sql(
    practice: SimpleNamespace, redis: FakeRedis
) -> None:
    from app.db.practice_resolution_cache import TTL_SECONDS  # noqa: PLC0415

    with _counting() as first:
        assert _resolve(practice.email) == (practice.id, practice.schema)
    with _counting() as second:
        assert _resolve(practice.email) == (practice.id, practice.schema)

    assert first.count > 0
    assert second.seen == []
    keys = redis.keys_with_prefix(_EMAIL_PREFIX)
    assert len(keys) == 1
    assert redis.ttls[keys[0]] == TTL_SECONDS
    # The address is hashed: no email sits in Redis as a key.
    assert all(practice.email not in key for key in (*redis.strings, *redis.sets))


def test_no_practice_is_never_cached(practice: SimpleNamespace, redis: FakeRedis) -> None:
    """A mapping written a moment ago must be seen on the next request."""
    assert _resolve(f"nobody-{practice.id}@example.invalid") is None
    assert redis.keys_with_prefix(_EMAIL_PREFIX) == []


def test_deactivating_the_practice_is_refused_on_the_next_resolution(
    engine: Engine, practice: SimpleNamespace, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    assert _resolve(practice.email) is not None
    with Session(engine) as session:
        row = session.get(PracticeRow, practice.id)
        assert row is not None
        row.is_active = False
        session.commit()

    assert redis.keys_with_prefix(_EMAIL_PREFIX) == []
    assert _resolve(practice.email) is None


def test_a_bulk_deactivation_drops_every_entry(
    engine: Engine, practice: SimpleNamespace, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    assert _resolve(practice.email) is not None
    with Session(engine) as session:
        session.execute(
            update(PracticeRow).where(PracticeRow.id == practice.id).values(is_active=False)
        )
        session.commit()

    assert redis.keys_with_prefix(_EMAIL_PREFIX) == []
    assert _resolve(practice.email) is None


def test_a_deactivation_inside_a_savepoint_drops_the_entry_at_commit(
    engine: Engine, practice: SimpleNamespace, redis: FakeRedis
) -> None:
    """A savepoint ending is not the transaction ending: its writes still commit."""
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    assert _resolve(practice.email) is not None
    with Session(engine) as session:
        with session.begin_nested():
            row = session.get(PracticeRow, practice.id)
            assert row is not None
            row.is_active = False
        session.commit()

    assert redis.keys_with_prefix(_EMAIL_PREFIX) == []
    assert _resolve(practice.email) is None


def test_removing_the_mapping_drops_the_entry(
    engine: Engine, practice: SimpleNamespace, redis: FakeRedis
) -> None:
    from app.db.platform_models import EmailTenantMappingRow  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    assert _resolve(practice.email) is not None
    with Session(engine) as session:
        mapping = session.get(EmailTenantMappingRow, practice.email)
        assert mapping is not None
        session.delete(mapping)
        session.commit()

    assert redis.keys_with_prefix(_EMAIL_PREFIX) == []
    assert _resolve(practice.email) is None


def test_a_change_that_rolls_back_leaves_the_entry(
    engine: Engine, practice: SimpleNamespace, redis: FakeRedis
) -> None:
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    assert _resolve(practice.email) is not None
    with Session(engine) as session:
        row = session.get(PracticeRow, practice.id)
        assert row is not None
        row.is_active = False
        session.flush()
        session.rollback()
        # A later commit on the same session must not carry the rolled-back drop.
        session.commit()

    assert len(redis.keys_with_prefix(_EMAIL_PREFIX)) == 1
    with _counting() as statements:
        assert _resolve(practice.email) == (practice.id, practice.schema)
    assert statements.seen == []


def test_without_redis_every_resolution_reads_the_database(
    practice: SimpleNamespace, no_redis: None
) -> None:
    for _ in range(2):
        with _counting() as statements:
            assert _resolve(practice.email) == (practice.id, practice.schema)
        assert statements.count > 0


def test_a_failing_redis_falls_back_to_the_database(
    practice: SimpleNamespace, redis: FakeRedis
) -> None:
    redis.fail = True
    with _counting() as statements:
        assert _resolve(practice.email) == (practice.id, practice.schema)
    assert statements.count > 0
