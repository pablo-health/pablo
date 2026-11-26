# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A reserved test address with no mapping resolves to no practice.

The test-tenant service provisions a practice for a reserved address
(``e2etest-<hex>`` / ``pentestuser-<hex>``) by writing its mapping before the
address first signs in. Deprovisioning that practice removes the user row and
the mapping, but the identity at the provider lives on. The next request from
that identity takes the auto-provision path again.

That path maps an ordinary new account onto the deployment's own practice,
and it used to do the same for the reserved address: the test identity landed
in the default practice and claimed a membership there, which then outranked
any later grant. These pin that it resolves to nothing instead, and that a
fresh provisioning still reaches it.

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


@pytest.fixture
def addresses(engine: Engine) -> Iterator[SimpleNamespace]:
    """A fresh reserved address and a fresh ordinary one; every row they leave is removed."""
    from app.db.platform_models import (
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeMemberRow,
        UserIdentityRow,
    )
    from sqlalchemy.orm import Session

    label = uuid.uuid4().hex[:8]
    reserved = f"e2etest-{label}@pablo.health"
    ordinary = f"ordinary-{label}@example.invalid"
    try:
        yield SimpleNamespace(reserved=reserved, ordinary=ordinary)
    finally:
        with Session(engine) as session:
            for email in (reserved, ordinary):
                ids = [u.id for u in session.query(PlatformUserRow).filter_by(email=email).all()]
                session.query(PracticeMemberRow).filter(PracticeMemberRow.user_id.in_(ids)).delete()
                session.query(UserIdentityRow).filter(UserIdentityRow.user_id.in_(ids)).delete()
                session.query(PlatformUserRow).filter_by(email=email).delete()
                session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.commit()


@pytest.fixture
def test_practice(engine: Engine) -> Iterator[SimpleNamespace]:
    """A provisioned practice with nobody mapped to it yet."""
    from app.db.platform_models import PracticeMemberRow, PracticeRow
    from app.db.provisioning import create_practice_schema
    from app.utcnow import utc_now
    from sqlalchemy.orm import Session

    label = uuid.uuid4().hex[:8]
    schema = f"practice_test_reserved_{label}"
    create_practice_schema(engine, schema)
    practice_id = f"reserved-{label}"
    with Session(engine) as session:
        session.add(
            PracticeRow(
                id=practice_id,
                name="Reserved",
                schema_name=schema,
                owner_email="",
                created_at=utc_now(),
            )
        )
        session.commit()
    try:
        yield SimpleNamespace(id=practice_id, schema=schema)
    finally:
        with Session(engine) as session:
            session.query(PracticeMemberRow).filter_by(practice_id=practice_id).delete()
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.commit()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@contextmanager
def _signed_in(app: FastAPI, uid: str, email: str) -> Iterator[Any]:
    """A client whose every request carries a verified token for *uid*."""
    from app.auth.providers import VerifiedIdentity
    from fastapi.testclient import TestClient

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


def _uid() -> str:
    return f"fb{uuid.uuid4().hex[:26]}"


def _code(response: Any) -> str | None:
    body = response.json()
    error = body.get("error") or (body.get("detail") or {}).get("error") or {}
    return error.get("code")


def _rows(engine: Engine, email: str) -> SimpleNamespace:
    """What the platform schema holds for *email*: users, mapping, memberships."""
    with engine.connect() as conn:
        users = conn.execute(
            text("SELECT count(*) FROM platform.users WHERE email = :e"), {"e": email}
        ).scalar()
        mapping = conn.execute(
            text("SELECT practice_id FROM platform.email_tenant_mappings WHERE email = :e"),
            {"e": email},
        ).scalar()
        members = conn.execute(
            text(
                "SELECT m.practice_id FROM platform.practice_members m "
                "JOIN platform.users u ON u.id = m.user_id WHERE u.email = :e"
            ),
            {"e": email},
        ).all()
    return SimpleNamespace(
        users=int(users or 0), mapping=mapping, members=sorted(r[0] for r in members)
    )


@pytest.mark.parametrize("restrict_signups", [True, False])
def test_an_unmapped_reserved_address_resolves_to_no_practice(
    fastapi_app: FastAPI,
    engine: Engine,
    addresses: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    restrict_signups: bool,
) -> None:
    """Gated or not, it gets an account and nothing to act on."""
    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "restrict_signups", restrict_signups)
    with _signed_in(fastapi_app, _uid(), addresses.reserved) as client:
        status = client.get("/api/users/me/status")
        patients = client.get("/api/patients")

    assert status.status_code == 200, status.text
    assert status.json().get("practice_id") is None
    assert patients.status_code == 403, patients.text
    assert _code(patients) == "NO_PRACTICE"
    rows = _rows(engine, addresses.reserved)
    assert rows.users == 1
    assert rows.mapping is None, "a reserved address was mapped onto a practice nobody provisioned"
    assert rows.members == []


def test_a_reserved_address_provisioned_again_reaches_its_new_practice(
    fastapi_app: FastAPI,
    engine: Engine,
    addresses: SimpleNamespace,
    test_practice: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stranded identity is not stuck: a new mapping is what it resolves to.

    Before the fix the first request claimed a default membership, and the
    membership outranked the mapping written afterwards.
    """
    from app.db.platform_models import EmailTenantMappingRow
    from app.settings import get_settings
    from app.utcnow import utc_now
    from sqlalchemy.orm import Session

    monkeypatch.setattr(get_settings(), "restrict_signups", True)
    uid = _uid()
    with _signed_in(fastapi_app, uid, addresses.reserved) as client:
        assert client.get("/api/users/me/status").json().get("practice_id") is None

    with Session(engine) as session:
        session.add(
            EmailTenantMappingRow(
                email=addresses.reserved,
                tenant_id=test_practice.id,
                practice_id=test_practice.id,
                created_at=utc_now(),
            )
        )
        session.commit()

    with _signed_in(fastapi_app, uid, addresses.reserved) as client:
        status = client.get("/api/users/me/status")

    assert status.status_code == 200, status.text
    assert status.json().get("practice_id") == test_practice.id
    assert _rows(engine, addresses.reserved).members == [test_practice.id]


def test_an_ordinary_address_on_an_ungated_deployment_still_maps_to_the_default(
    fastapi_app: FastAPI,
    engine: Engine,
    addresses: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import DEFAULT_PRACTICE_ID
    from app.settings import get_settings

    monkeypatch.setattr(get_settings(), "restrict_signups", False)
    with _signed_in(fastapi_app, _uid(), addresses.ordinary) as client:
        status = client.get("/api/users/me/status")

    assert status.status_code == 200, status.text
    assert status.json().get("practice_id") == DEFAULT_PRACTICE_ID
    assert _rows(engine, addresses.ordinary).mapping == DEFAULT_PRACTICE_ID
