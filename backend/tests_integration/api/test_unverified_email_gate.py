# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An account cannot act on a practice before its email is verified (PABLO-nhwa).

A practice is mapped to its owner's or an invitee's email before anyone has
signed in, and a password account can be registered for any address. Until the
identity provider confirms the account holder controls the address, it is only
what someone typed, so sign-in refuses it before anything is written — and the
routes that resolve a practice from the stored ``user.email`` are never reached.

These go through the real app, middleware, identity mapping and a practice
schema provisioned the production way. Only the identity provider's signature
check is stood in for.

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
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

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

_PASSWORD = {"sign_in_provider": "password"}
_GOOGLE = {"sign_in_provider": "google.com"}


@pytest.fixture
def practice(engine: Engine) -> Iterator[SimpleNamespace]:
    """A practice as payment leaves it: provisioned, with its owner's email mapped and no user."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
        UserIdentityRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    label = uuid.uuid4().hex[:8]
    schema = f"practice_test_verify_{label}"
    create_practice_schema(engine, schema)
    practice_id = f"verify-{label}"
    email = f"owner-{label}@example.invalid"
    with Session(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Verify",
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
            user_ids = [u.id for u in session.query(PlatformUserRow).filter_by(email=email).all()]
            session.query(UserIdentityRow).filter(UserIdentityRow.user_id.in_(user_ids)).delete()
            session.query(PlatformUserRow).filter_by(email=email).delete()
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.commit()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@contextmanager
def _signed_in(
    app: FastAPI, uid: str, email: str, *, verified: bool, firebase: dict[str, Any] | None = None
) -> Iterator[Any]:
    """A client whose every request carries a token for *uid*."""
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=uid,
        email=email,
        email_verified=verified,
        mfa_satisfied=True,
        claims={
            "uid": uid,
            "sub": uid,
            "email": email,
            "email_verified": verified,
            "auth_time": 1_700_000_000,
            "firebase": firebase or _PASSWORD,
        },
    )
    with (
        patch("app.auth.service.verify_token", return_value=identity),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        client.headers["Authorization"] = "Bearer test-token"
        yield client


def _uid() -> str:
    return f"fb{uuid.uuid4().hex[:26]}"


def _rows_for(engine: Engine, uid: str, email: str) -> tuple[int, int]:
    """(users with *email*, identities for *uid*) in the platform schema."""
    with engine.connect() as conn:
        users = conn.execute(
            text("SELECT count(*) FROM platform.users WHERE email = :e"), {"e": email}
        ).scalar()
        identities = conn.execute(
            text("SELECT count(*) FROM platform.user_identities WHERE subject_id = :s"),
            {"s": uid},
        ).scalar()
    return int(users or 0), int(identities or 0)


def _code(response: Any) -> str | None:
    body = response.json()
    error = body.get("error") or (body.get("detail") or {}).get("error") or {}
    return error.get("code")


def test_an_unverified_sign_in_is_refused_before_any_row_is_written(
    fastapi_app: FastAPI, engine: Engine, practice: SimpleNamespace
) -> None:
    uid = _uid()
    with _signed_in(fastapi_app, uid, practice.email, verified=False) as client:
        response = client.get("/api/users/me/status")

    assert response.status_code == 403
    assert _code(response) == "EMAIL_NOT_VERIFIED"
    assert _rows_for(engine, uid, practice.email) == (0, 0)


@pytest.mark.parametrize(
    "call",
    [
        ("GET", "/api/portal/settings", None),
        ("PUT", "/api/portal/settings", {"enabled": True}),
        ("GET", "/api/portal/invite-template", None),
        ("PUT", "/api/portal/invite-template", {"subject": "Hi", "body": "Join {link}"}),
    ],
)
def test_an_unverified_account_for_an_invited_address_cannot_reach_practice_settings(
    fastapi_app: FastAPI,
    engine: Engine,
    practice: SimpleNamespace,
    call: tuple[str, str, dict[str, Any] | None],
) -> None:
    """And the real holder of the address can still sign in afterwards."""
    method, path, body = call
    squatter = _uid()
    with _signed_in(fastapi_app, squatter, practice.email, verified=False) as client:
        response = client.request(method, path, json=body)

    assert response.status_code == 403
    assert _code(response) == "EMAIL_NOT_VERIFIED"
    assert _rows_for(engine, squatter, practice.email) == (0, 0)

    invitee = _uid()
    with _signed_in(fastapi_app, invitee, practice.email, verified=True) as client:
        status = client.get("/api/users/me/status")
    assert status.status_code == 200, status.text
    assert status.json()["practice_id"] == practice.id


def test_an_existing_account_is_held_to_verification_too(
    fastapi_app: FastAPI, engine: Engine, practice: SimpleNamespace
) -> None:
    """A user row created before this check does not carry access past it."""
    uid = _uid()
    with _signed_in(fastapi_app, uid, practice.email, verified=True) as client:
        assert client.get("/api/users/me/status").status_code == 200

    with _signed_in(fastapi_app, uid, practice.email, verified=False) as client:
        response = client.get("/api/portal/settings")
    assert response.status_code == 403
    assert _code(response) == "EMAIL_NOT_VERIFIED"


def test_google_sign_in_is_unaffected(
    fastapi_app: FastAPI, engine: Engine, practice: SimpleNamespace
) -> None:
    uid = _uid()
    with _signed_in(fastapi_app, uid, practice.email, verified=True, firebase=_GOOGLE) as client:
        status = client.get("/api/users/me/status")
        settings = client.get("/api/portal/settings")

    assert status.status_code == 200, status.text
    assert status.json()["practice_id"] == practice.id
    assert settings.status_code == 200, settings.text


def test_an_unverified_session_can_still_ask_whether_it_is_alive(
    fastapi_app: FastAPI, practice: SimpleNamespace
) -> None:
    """The idle timer polls this while the clinician goes to verify."""
    with _signed_in(fastapi_app, _uid(), practice.email, verified=False) as client:
        response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text


def test_pay_first_sign_up_then_verify_then_onboard(
    fastapi_app: FastAPI, engine: Engine, practice: SimpleNamespace
) -> None:
    """Payment provisioned the practice; the clinician creates a password, verifies, onboards.

    The onboarding write lands in the practice's own audit log, never the template's.
    """
    uid = _uid()
    with _signed_in(fastapi_app, uid, practice.email, verified=False) as client:
        refused = client.post(
            "/api/users/me/acknowledge-security-guide", json={"version": "2026-01-01"}
        )
    assert refused.status_code == 403
    assert _code(refused) == "EMAIL_NOT_VERIFIED"
    assert _rows_for(engine, uid, practice.email) == (0, 0)

    with _signed_in(fastapi_app, uid, practice.email, verified=True) as client:
        status = client.get("/api/users/me/status")
        acknowledged = client.post(
            "/api/users/me/acknowledge-security-guide", json={"version": "2026-01-01"}
        )
    assert status.status_code == 200, status.text
    assert status.json()["practice_id"] == practice.id
    assert acknowledged.status_code == 200, acknowledged.text

    users, _ = _rows_for(engine, uid, practice.email)
    assert users == 1
    with engine.connect() as conn:
        user_id = conn.execute(
            text("SELECT id FROM platform.users WHERE email = :e"), {"e": practice.email}
        ).scalar()
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"), {"u": str(user_id)}
        )
        query = (
            "SELECT count(*) FROM {}.audit_logs WHERE CAST(user_id AS VARCHAR) = :u AND action = :a"
        )
        args = {"u": str(user_id), "a": "onboarding_security_guide_acknowledged"}
        in_practice = conn.execute(text(query.format(f'"{practice.schema}"')), args).scalar()
        in_template = conn.execute(text(query.format("practice")), args).scalar()
        conn.execute(text("RESET app.current_user_id"))
    assert in_practice == 1
    assert in_template == 0


def test_a_first_sign_in_reads_the_practice_it_was_just_mapped_to(
    fastapi_app: FastAPI, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ungated deployment maps a new account on its first request.

    The middleware resolved no practice for that request, so it starts with no
    practice schema; the status check it makes reads practice tables and must
    find the practice, not fail.
    """
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        UserIdentityRow,
    )
    from app.settings import get_settings  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    monkeypatch.setattr(get_settings(), "restrict_signups", False)
    uid = _uid()
    email = f"first-{uuid.uuid4().hex[:8]}@example.invalid"
    try:
        with _signed_in(fastapi_app, uid, email, verified=True) as client:
            response = client.get("/api/users/me/status")
        assert response.status_code == 200, response.text
        assert response.json().get("practice_id")
    finally:
        with Session(engine) as session:
            ids = [u.id for u in session.query(PlatformUserRow).filter_by(email=email).all()]
            session.query(UserIdentityRow).filter(UserIdentityRow.user_id.in_(ids)).delete()
            session.query(PlatformUserRow).filter_by(email=email).delete()
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.commit()
