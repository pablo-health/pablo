# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every sign-in leaves a ``signed_in`` row, proven against the real app + Postgres.

``POST /api/ext/auth/check-status`` is what the identity provider's sign-in
hook calls on every sign-in, after every factor has been verified. It is the
one place a Google or email sign-in finished with an authenticator-app code
reaches the backend at all, so it is where the sign-in is recorded.

These go through the real middleware, the real identity mapping, the real
practice resolution and practice schemas provisioned the production way (RLS
on), because each of those is somewhere the row can silently fail to land:

* the hook sends the identity provider's uid, which is not the user's id — a
  lookup that skips the identity mapping finds nobody and writes nothing;
* the request has no clinician session, so it starts on the default schema —
  a write that is not re-scoped lands in the wrong place or is refused;
* a failed audit INSERT aborts the request transaction, and the commit at the
  end of the request then fails the very check the write was meant to stay
  out of.

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
from sqlalchemy import String, cast, column, select, table, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.audit import AuditLogEntry
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_CHECK_STATUS = "/api/ext/auth/check-status"


@contextmanager
def _practice(engine: Engine, label: str) -> Iterator[SimpleNamespace]:
    """A practice provisioned the production way, with its platform row."""
    from app.db.platform_models import PracticeRow  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    # The RLS policies reference ``has_patient_access`` unqualified (it lives in
    # ``practice``), so warm the pool with a search_path that includes it.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_signin_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    practice_id = f"signin-{label}-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name=f"Sign-in {label}",
                schema_name=schema,
                owner_email=f"{practice_id}@example.invalid",
                created_at=utc_now(),
            )
        )
        session.commit()
    try:
        yield SimpleNamespace(id=practice_id, schema=schema)
    finally:
        with Session(engine) as session:
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.commit()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@pytest.fixture(scope="module")
def own_practice(engine: Engine) -> Iterator[SimpleNamespace]:
    with _practice(engine, "own") as practice:
        yield practice


@pytest.fixture(scope="module")
def other_practice(engine: Engine) -> Iterator[SimpleNamespace]:
    """A second practice, so a row landing in the wrong one would show."""
    with _practice(engine, "other") as practice:
        yield practice


@contextmanager
def _clinician(
    engine: Engine, practice: SimpleNamespace, *, status: str = "approved"
) -> Iterator[SimpleNamespace]:
    """A user in *practice*, signed up through the identity provider.

    The user's id and the provider's uid are different values, as they are in
    production: the hook only ever knows the uid.
    """
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        UserIdentityRow,
    )
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    user_id = str(uuid.uuid4())
    uid = f"fb{uuid.uuid4().hex[:26]}"
    email = f"signin-{user_id[:8]}@example.invalid"
    now = utc_now()
    with Session(engine) as session:
        session.add(
            PlatformUserRow(id=user_id, email=email, name="Sign-in", created_at=now, status=status)
        )
        session.flush()
        session.add(
            UserIdentityRow(provider="firebase", subject_id=uid, user_id=user_id, linked_at=now)
        )
        session.add(
            EmailTenantMappingRow(
                email=email, tenant_id=practice.id, practice_id=practice.id, created_at=now
            )
        )
        session.commit()
    try:
        yield SimpleNamespace(user_id=user_id, uid=uid)
    finally:
        with Session(engine) as session:
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(UserIdentityRow).filter_by(user_id=user_id).delete()
            session.query(PlatformUserRow).filter_by(id=user_id).delete()
            session.commit()


def _sign_in_rows(engine: Engine, schema: str, user_id: str) -> list[dict[str, Any]]:
    """The user's ``signed_in`` rows in *schema*, read as that user (RLS armed)."""
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id})
        columns = ("actor_type", "resource_type", "resource_id", "ip_address", "user_agent")
        audit_logs = table(
            "audit_logs",
            *(column(c) for c in (*columns, "changes", "user_id", "action")),
            schema=schema,
        )
        rows = conn.execute(
            select(*(audit_logs.c[c] for c in (*columns, "changes"))).where(
                cast(audit_logs.c.user_id, String) == user_id,
                audit_logs.c.action == "signed_in",
            )
        )
        return [dict(r._mapping) for r in rows]


def _check_status(app: FastAPI, uid: str) -> tuple[int, Any]:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app) as client:
        response = client.post(_CHECK_STATUS, json={"uid": uid})
    return response.status_code, response.json()


class TestCheckStatusRecordsSignIn:
    def test_enabled_user_gets_one_row_in_their_own_practice(
        self,
        engine: Engine,
        fastapi_app: FastAPI,
        own_practice: SimpleNamespace,
        other_practice: SimpleNamespace,
    ) -> None:
        with _clinician(engine, own_practice) as user:
            assert _check_status(fastapi_app, user.uid) == (200, {"disabled": False})

            rows = _sign_in_rows(engine, own_practice.schema, user.user_id)
            assert rows == [
                {
                    "actor_type": "clinician",
                    "resource_type": "self",
                    "resource_id": user.user_id,
                    # The request came from the identity provider's hook, so
                    # its address and agent are not the clinician's.
                    "ip_address": None,
                    "user_agent": None,
                    "changes": None,
                }
            ]
            assert _sign_in_rows(engine, other_practice.schema, user.user_id) == []
            assert _sign_in_rows(engine, "practice", user.user_id) == []

    def test_each_sign_in_is_its_own_row(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice) as user:
            _check_status(fastapi_app, user.uid)
            _check_status(fastapi_app, user.uid)
            assert len(_sign_in_rows(engine, own_practice.schema, user.user_id)) == 2

    def test_failed_audit_write_still_answers_normally(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        """A failed INSERT must not turn into a failed check.

        Failing in the flush (not by raising from Python before any SQL runs)
        is the point: a failed flush leaves the request's session needing a
        rollback, and that is what fails the commit at the end of the request.
        Here the row is written twice under one id, so the second flush breaks
        the primary key.
        """
        from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415

        real_append = PostgresAuditRepository.append

        def _failing_append(self: PostgresAuditRepository, entry: AuditLogEntry) -> None:
            real_append(self, entry)
            self._session.expunge_all()
            real_append(self, entry)

        with (
            _clinician(engine, own_practice) as user,
            patch.object(PostgresAuditRepository, "append", _failing_append),
        ):
            assert _check_status(fastapi_app, user.uid) == (200, {"disabled": False})
        assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []

    def test_unknown_uid_writes_nothing(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        # A brand-new account's first sign-in: no user, no mapping, no practice.
        # The only id a stray row could carry is the uid itself.
        uid = f"fb{uuid.uuid4().hex[:26]}"
        assert _check_status(fastapi_app, uid) == (200, {"disabled": False})
        assert _sign_in_rows(engine, "practice", uid) == []
        assert _sign_in_rows(engine, own_practice.schema, uid) == []

    def test_disabled_user_is_refused_and_not_recorded(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice, status="disabled") as user:
            assert _check_status(fastapi_app, user.uid) == (200, {"disabled": True})
            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []

    def test_caller_without_service_identity_is_rejected_and_not_recorded(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        from app.settings import get_settings  # noqa: PLC0415

        real = get_settings()
        deployed = real.model_copy(
            update={
                "environment": "production",
                "backend_base_url": "https://api.example.invalid",
                "blocking_function_service_account": "hook@example.invalid",
            }
        )
        with (
            _clinician(engine, own_practice) as user,
            patch("app.routes.ext_auth.get_settings", return_value=deployed),
        ):
            status_code, _ = _check_status(fastapi_app, user.uid)
            assert status_code == 403
            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []
