# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice admins, against a real provisioned practice schema.

Two clinicians in one practice, the owner and a colleague, both signing in
through the practice's address mapping:

* the owner grants the colleague admin; the grant lands in the practice's own
  ``practice_admins`` table, which a freshly provisioned schema has (so the
  template carries it);
* the colleague then passes ``require_practice_admin`` and can rename the
  practice, and ``/me/status`` says so;
* the owner revokes it, and both stop at once;
* the colleague, admin or not, can never grant or revoke.

The table is registered not-row-scoped, so a row policy cannot stand between
the owner and the list; this module is where that is proven on a real schema.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from app.api_errors import register_exception_handlers
from app.auth.service import (
    get_current_user,
    get_current_user_no_mfa,
    require_active_subscription,
)
from app.db import arm_current_user_id, set_tenant_schema
from app.db.provisioning import create_practice_schema
from app.models import User
from app.routes import practice_admins as practice_admin_routes
from app.routes import users as users_routes
from app.routes.users import require_practice_admin
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_admins_{_SUFFIX}"
_PRACTICE_ID = f"admins-test-{_SUFFIX}"
_OWNER_ID = str(uuid.uuid4())
_COLLEAGUE_ID = str(uuid.uuid4())
_OWNER_EMAIL = f"owner-{_SUFFIX}@example.test"
_COLLEAGUE_EMAIL = f"colleague-{_SUFFIX}@example.test"


def _user(user_id: str, email: str, name: str) -> User:
    return User(
        id=user_id,
        email=email,
        name=name,
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


OWNER = _user(_OWNER_ID, _OWNER_EMAIL, "Olive Owner")
COLLEAGUE = _user(_COLLEAGUE_ID, _COLLEAGUE_EMAIL, "Casey Colleague")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """The real database, migrated, with this module's practice and two clinicians."""
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    create_practice_schema(eng, _SCHEMA)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                " owner_user_id, product, status, is_active, created_at)"
                " VALUES (:i, 'Admins Test', :s, :e, CAST(:o AS uuid), 'pablo', 'active',"
                " true, now())"
            ),
            {"i": _PRACTICE_ID, "s": _SCHEMA, "e": _OWNER_EMAIL, "o": _OWNER_ID},
        )
        for user_id, email, name in (
            (_OWNER_ID, _OWNER_EMAIL, "Olive Owner"),
            (_COLLEAGUE_ID, _COLLEAGUE_EMAIL, "Casey Colleague"),
        ):
            conn.execute(
                text(
                    "INSERT INTO platform.users (id, email, name, created_at, status,"
                    " is_platform_admin, chat_quality_review_opt_in,"
                    " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                    " VALUES (CAST(:i AS uuid), :e, :n, now(), 'approved', false,"
                    " false, false, false)"
                ),
                {"i": user_id, "e": email, "n": name},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.email_tenant_mappings"
                    " (email, tenant_id, practice_id, created_at) VALUES (:e, 't', :p, now())"
                ),
                {"e": email, "p": _PRACTICE_ID},
            )
    yield eng
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM platform.email_tenant_mappings WHERE practice_id = :p"),
            {"p": _PRACTICE_ID},
        )
        conn.execute(
            text("DELETE FROM platform.companion_practice_slugs WHERE practice_id = :p"),
            {"p": _PRACTICE_ID},
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :p"), {"p": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": [_OWNER_ID, _COLLEAGUE_ID]},
        )
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
    eng.dispose()


@pytest.fixture
def harness(engine: Engine) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    set_tenant_schema(session, _SCHEMA)
    acting: dict[str, User] = {"user": OWNER}

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(practice_admin_routes.router)
    app.include_router(users_routes.router)

    @app.get("/gated")
    def gated(practice: Any = Depends(require_practice_admin)) -> dict[str, str]:
        return {"practice_id": practice.id}

    app.dependency_overrides[get_current_user] = lambda: acting["user"]
    app.dependency_overrides[get_current_user_no_mfa] = lambda: acting["user"]
    app.dependency_overrides[require_active_subscription] = lambda: acting["user"]
    with (
        patch("app.db.get_db_session", return_value=session),
        patch.object(practice_admin_routes, "get_db_session", return_value=session),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        try:
            yield {"client": client, "session": session, "acting": acting}
        finally:
            session.rollback()
            session.execute(text(f"DELETE FROM {_SCHEMA}.practice_admins"))  # noqa: S608 — schema name, not user input
            session.execute(
                text("UPDATE platform.practices SET name = 'Admins Test' WHERE id = :p"),
                {"p": _PRACTICE_ID},
            )
            session.commit()
            session.close()


def _as(harness: dict[str, Any], user: User) -> TestClient:
    """Sign in as ``user``: the caller, and the RLS principal the middleware arms."""
    harness["acting"]["user"] = user
    arm_current_user_id(harness["session"], user.id)
    return harness["client"]  # type: ignore[no-any-return]


def _grant_rows(session: Session) -> list[str]:
    return [
        str(row)
        for row in session.execute(
            text(f"SELECT user_id FROM {_SCHEMA}.practice_admins")  # noqa: S608 — schema name, not user input
        ).scalars()
    ]


def _rename(client: TestClient, name: str) -> int:
    return client.patch("/api/users/me/professional-info", json={"practice_name": name}).status_code


def test_the_owner_sees_both_clinicians(harness: dict[str, Any]) -> None:
    response = _as(harness, OWNER).get("/api/practice/admins")
    assert response.status_code == 200, response.text
    assert [(c["email"], c["is_owner"], c["is_admin"]) for c in response.json()["clinicians"]] == [
        (_OWNER_EMAIL, True, True),
        (_COLLEAGUE_EMAIL, False, False),
    ]


def test_a_colleague_is_refused_until_the_owner_grants_admin(harness: dict[str, Any]) -> None:
    colleague = _as(harness, COLLEAGUE)
    assert colleague.get("/gated").status_code == 403
    assert _rename(colleague, "Renamed By Colleague") == 403
    assert colleague.get("/api/users/me/status").json().get("is_practice_admin") is False

    granted = _as(harness, OWNER).put(f"/api/practice/admins/{_COLLEAGUE_ID}")
    assert granted.status_code == 200, granted.text
    assert _grant_rows(harness["session"]) == [_COLLEAGUE_ID]

    colleague = _as(harness, COLLEAGUE)
    assert colleague.get("/gated").json() == {"practice_id": _PRACTICE_ID}
    assert _rename(colleague, "Renamed By Colleague") == 200
    assert colleague.get("/api/users/me/status").json()["is_practice_admin"] is True


def test_revoking_takes_the_ability_away(harness: dict[str, Any]) -> None:
    owner = _as(harness, OWNER)
    owner.put(f"/api/practice/admins/{_COLLEAGUE_ID}")
    assert owner.delete(f"/api/practice/admins/{_COLLEAGUE_ID}").status_code == 200
    assert _grant_rows(harness["session"]) == []

    colleague = _as(harness, COLLEAGUE)
    assert colleague.get("/gated").status_code == 403
    assert _rename(colleague, "After Revoke") == 403


def test_an_admin_cannot_grant_or_revoke(harness: dict[str, Any]) -> None:
    _as(harness, OWNER).put(f"/api/practice/admins/{_COLLEAGUE_ID}")
    colleague = _as(harness, COLLEAGUE)
    assert colleague.get("/api/practice/admins").status_code == 403
    assert colleague.delete(f"/api/practice/admins/{_COLLEAGUE_ID}").status_code == 403
    assert colleague.put(f"/api/practice/admins/{_OWNER_ID}").status_code == 403
    assert _grant_rows(harness["session"]) == [_COLLEAGUE_ID]


def test_nobody_grants_or_revokes_the_owner(harness: dict[str, Any]) -> None:
    owner = _as(harness, OWNER)
    assert owner.put(f"/api/practice/admins/{_OWNER_ID}").status_code == 400
    assert owner.delete(f"/api/practice/admins/{_OWNER_ID}").status_code == 400
    assert _grant_rows(harness["session"]) == []
