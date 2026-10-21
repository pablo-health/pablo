# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every sign-in leaves exactly one ``signed_in`` row, proven against the real app + Postgres.

The backend records a sign-in on the first authenticated request it makes
(``app/auth/sign_in_audit.py``), keyed by the token's subject and
``auth_time``. A refreshed token keeps its ``auth_time``, so it is the same
sign-in and must write nothing; signing in again must write a new row. The
optional ``beforeSignIn`` hook's ``/api/ext/auth/check-status`` no longer
writes the row, so a deployment that still has the hook registered does not
get two.

These go through the real middleware, the real identity mapping, the real
practice resolution and practice schemas provisioned the production way (RLS
on). Only the identity provider's signature check is stood in for, since there
is no provider here. Each of the rest is somewhere the row can silently fail
to land, land twice, or land in the wrong practice:

* the token carries the identity provider's uid, which is not the user's id —
  a write keyed on the wrong one is invisible to the user's own audit view;
* the user's practice is resolved from platform tables, not from the request —
  a write that is not re-scoped lands in the wrong place or is refused;
* a failed audit INSERT poisons the transaction it ran in — if that were the
  request's, the request would fail after all.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
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
_ME_STATUS = "/api/users/me/status"
_USER_AGENT = "sign-in-audit-test"


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


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    """Each test starts as a freshly started instance would."""
    from app.auth.sign_in_audit import forget_all  # noqa: PLC0415

    forget_all()
    yield
    forget_all()


@contextmanager
def _clinician(
    engine: Engine, practice: SimpleNamespace, *, status: str = "approved"
) -> Iterator[SimpleNamespace]:
    """A user in *practice*, signed up through the identity provider.

    The user's id and the provider's uid are different values, as they are in
    production.
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
        yield SimpleNamespace(user_id=user_id, uid=uid, email=email)
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
        columns = ("actor_type", "resource_type", "resource_id", "user_agent")
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


def _request_as(
    app: FastAPI, uid: str, email: str, *, auth_time: int, issued_at: int | None = None
) -> tuple[int, Any]:
    """One authenticated request, with a token for *uid* from the sign-in at *auth_time*.

    ``issued_at`` stands in for a refresh: a refreshed token is issued later
    but keeps the ``auth_time`` of the sign-in it came from.
    """
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=uid,
        email=email,
        mfa_satisfied=True,
        claims={
            "uid": uid,
            "sub": uid,
            "email": email,
            "iat": issued_at or auth_time,
            "auth_time": auth_time,
            "firebase": {"sign_in_provider": "password", "sign_in_second_factor": "totp"},
        },
    )
    with (
        patch("app.auth.service.verify_token", return_value=identity),
        TestClient(app) as client,
    ):
        response = client.get(
            _ME_STATUS,
            headers={"Authorization": "Bearer test-token", "User-Agent": _USER_AGENT},
        )
    return response.status_code, response.json()


def _sign_in_key(uid: str, auth_time: int) -> str:
    from app.auth.idle_session import session_log_key  # noqa: PLC0415

    key = session_log_key({"uid": uid, "auth_time": auth_time})
    assert key is not None
    return key


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


class TestSignInIsRecordedByTheBackend:
    def test_a_sign_in_gets_one_row_in_their_own_practice(
        self,
        engine: Engine,
        fastapi_app: FastAPI,
        own_practice: SimpleNamespace,
        other_practice: SimpleNamespace,
    ) -> None:
        with _clinician(engine, own_practice) as user:
            signed_in_at = _now()
            status_code, _ = _request_as(fastapi_app, user.uid, user.email, auth_time=signed_in_at)
            assert status_code == 200

            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == [
                {
                    "actor_type": "clinician",
                    "resource_type": "self",
                    "resource_id": user.user_id,
                    # The request comes from the clinician's own device, so its
                    # agent is theirs (the hook's request carried none).
                    "user_agent": _USER_AGENT,
                    "changes": {"sign_in": _sign_in_key(user.uid, signed_in_at)},
                }
            ]
            assert _sign_in_rows(engine, other_practice.schema, user.user_id) == []
            assert _sign_in_rows(engine, "practice", user.user_id) == []

    def test_a_token_refresh_writes_nothing(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        from app.auth.sign_in_audit import forget_all  # noqa: PLC0415

        with _clinician(engine, own_practice) as user:
            signed_in_at = _now() - 600
            _request_as(fastapi_app, user.uid, user.email, auth_time=signed_in_at)
            # Same instance, refreshed token.
            _request_as(fastapi_app, user.uid, user.email, auth_time=signed_in_at, issued_at=_now())
            # Another (or a restarted) instance: nothing remembered in-process,
            # so only the audit log itself can say the sign-in is recorded.
            forget_all()
            status_code, _ = _request_as(
                fastapi_app, user.uid, user.email, auth_time=signed_in_at, issued_at=_now()
            )
            assert status_code == 200
            assert len(_sign_in_rows(engine, own_practice.schema, user.user_id)) == 1

    def test_signing_in_again_is_its_own_row(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice) as user:
            first = _now() - 3600
            _request_as(fastapi_app, user.uid, user.email, auth_time=first)
            _request_as(fastapi_app, user.uid, user.email, auth_time=first + 60)
            rows = _sign_in_rows(engine, own_practice.schema, user.user_id)
            assert sorted(r["changes"]["sign_in"] for r in rows) == sorted(
                [_sign_in_key(user.uid, first), _sign_in_key(user.uid, first + 60)]
            )

    def test_failed_audit_write_does_not_fail_the_request_and_is_retried(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        """A failed INSERT must not turn into a failed request, nor be forgotten.

        Failing in the flush (not by raising from Python before any SQL runs)
        is the point: a failed flush is what poisons a transaction. Here the
        row is written twice under one id, so the second flush breaks the
        primary key.
        """
        from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415

        real_append = PostgresAuditRepository.append

        def _failing_append(self: PostgresAuditRepository, entry: AuditLogEntry) -> None:
            real_append(self, entry)
            self._session.expunge_all()
            real_append(self, entry)

        with _clinician(engine, own_practice) as user:
            signed_in_at = _now()
            with patch.object(PostgresAuditRepository, "append", _failing_append):
                status_code, _ = _request_as(
                    fastapi_app, user.uid, user.email, auth_time=signed_in_at
                )
            assert status_code == 200
            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []

            _request_as(fastapi_app, user.uid, user.email, auth_time=signed_in_at)
            assert len(_sign_in_rows(engine, own_practice.schema, user.user_id)) == 1

    def test_disabled_user_is_refused_and_not_recorded(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice, status="disabled") as user:
            status_code, body = _request_as(fastapi_app, user.uid, user.email, auth_time=_now())
            assert status_code == 403
            assert body["detail"]["error"]["code"] == "USER_DISABLED"
            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []

    def test_non_allowlisted_user_is_refused_and_never_created(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        """With no beforeCreate hook to stop the account, the backend still refuses it."""
        from app.db.platform_models import UserIdentityRow  # noqa: PLC0415
        from app.settings import get_settings  # noqa: PLC0415
        from sqlalchemy.orm import Session  # noqa: PLC0415

        restricted = get_settings().model_copy(update={"restrict_signups": True})
        uid = f"fb{uuid.uuid4().hex[:26]}"
        email = f"stranger-{uuid.uuid4().hex[:8]}@example.invalid"
        with patch("app.auth.service.get_settings", return_value=restricted):
            status_code, body = _request_as(fastapi_app, uid, email, auth_time=_now())
        assert status_code == 403
        assert body["detail"]["error"]["code"] == "SIGNUP_NOT_ALLOWED"
        with Session(engine) as session:
            assert session.query(UserIdentityRow).filter_by(subject_id=uid).count() == 0
        assert _sign_in_rows(engine, "practice", uid) == []
        assert _sign_in_rows(engine, own_practice.schema, uid) == []


class TestCheckStatusStillAnswersButNoLongerRecords:
    """The optional hook keeps working for deployments that still register it."""

    def test_enabled_user_is_allowed_and_no_row_is_written(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice) as user:
            assert _check_status(fastapi_app, user.uid) == (200, {"disabled": False})
            assert _sign_in_rows(engine, own_practice.schema, user.user_id) == []

    def test_hook_then_first_request_is_still_one_row(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        """What a deployment with the hook still registered sees on each sign-in."""
        with _clinician(engine, own_practice) as user:
            _check_status(fastapi_app, user.uid)
            _request_as(fastapi_app, user.uid, user.email, auth_time=_now())
            assert len(_sign_in_rows(engine, own_practice.schema, user.user_id)) == 1

    def test_unknown_uid_is_not_disabled(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        # A brand-new account's first sign-in: no user, no mapping, no practice.
        uid = f"fb{uuid.uuid4().hex[:26]}"
        assert _check_status(fastapi_app, uid) == (200, {"disabled": False})
        assert _sign_in_rows(engine, "practice", uid) == []
        assert _sign_in_rows(engine, own_practice.schema, uid) == []

    def test_disabled_user_is_refused(
        self, engine: Engine, fastapi_app: FastAPI, own_practice: SimpleNamespace
    ) -> None:
        with _clinician(engine, own_practice, status="disabled") as user:
            assert _check_status(fastapi_app, user.uid) == (200, {"disabled": True})

    def test_caller_without_service_identity_is_rejected(
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
