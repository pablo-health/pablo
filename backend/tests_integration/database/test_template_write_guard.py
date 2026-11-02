# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""No signed-in request runs on the template schema, and no request writes to it (PABLO-nhwa).

The ``practice`` schema is the template every new practice is built from. It
has no row security. A signed-in request whose identity resolves to no
practice used to run on it, and a write flushed before the commit slipped
past the commit-time check — which is how onboarding rows from an unverified
account ended up in it.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy import insert, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


def _search_path(session: Session) -> str:
    return str(session.execute(text("SHOW search_path")).scalar())


def _request(identity: object | None) -> SimpleNamespace:
    headers = {"authorization": "Bearer test-token"} if identity is not None else {}
    return SimpleNamespace(headers=headers, state=SimpleNamespace())


def _identity(email: str, *, verified: bool) -> object:
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415

    return VerifiedIdentity(
        provider="firebase",
        subject_id="uid-guard",
        email=email,
        email_verified=verified,
        mfa_satisfied=True,
        claims={"uid": "uid-guard", "email": email, "email_verified": verified},
    )


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    from app.db import _current_tenant_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as s:
        yield s
        s.rollback()
    _current_tenant_schema.set(None)


def _prepare(session: Session, identity: object | None) -> tuple[str | None, str | None]:
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415

    with patch("app.auth.service.verify_token", return_value=identity):
        return DatabaseSessionMiddleware._prepare(_request(identity), session)  # type: ignore[arg-type] -- the prelude reads only headers and state


@pytest.mark.parametrize(
    ("email", "verified", "reason"),
    [
        ("anyone@example.invalid", False, "identity-email-not-verified"),
        (
            f"nobody-{uuid.uuid4().hex[:8]}@example.invalid",
            True,
            "no-practice-mapping-for-identity",
        ),
    ],
)
def test_a_signed_in_request_with_no_practice_runs_on_no_practice_schema(
    session: Session, email: str, verified: bool, reason: str
) -> None:
    schema, unresolved = _prepare(session, _identity(email, verified=verified))

    assert (schema, unresolved) == (None, reason)
    assert _search_path(session) == "platform, public"


def test_an_unauthenticated_request_keeps_the_default_schema(session: Session) -> None:
    schema, unresolved = _prepare(session, None)

    assert (schema, unresolved) == ("practice", None)
    assert _search_path(session).startswith("practice,")


def test_a_resolved_request_runs_on_its_practice(session: Session) -> None:
    from app.db import DEFAULT_PRACTICE_OWN_SCHEMA  # noqa: PLC0415

    with patch(
        "app.auth.service._resolve_practice_from_email",
        return_value=("default", DEFAULT_PRACTICE_OWN_SCHEMA),
    ):
        schema, unresolved = _prepare(session, _identity("owner@example.invalid", verified=True))

    assert (schema, unresolved) == (DEFAULT_PRACTICE_OWN_SCHEMA, None)
    assert _search_path(session).startswith(f"{DEFAULT_PRACTICE_OWN_SCHEMA},")


_ROUTE = {"ehr_system": "guard", "route_name": "r"}


def _stamps() -> dict[str, object]:
    from app.utcnow import utc_now  # noqa: PLC0415

    now = utc_now()
    return {"created_at": now, "updated_at": now}


def _route_row() -> object:
    from app.db.models import EhrRouteRow  # noqa: PLC0415

    return EhrRouteRow(id=f"guard-{uuid.uuid4().hex[:12]}", **_ROUTE, **_stamps())


def _template_rows(engine: Engine) -> int:
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM practice.ehr_routes WHERE ehr_system = 'guard'")
        ).scalar()
    return int(count or 0)


def test_a_flushed_write_to_the_template_is_refused_through_the_middleware(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The commit-time check saw nothing once a repository had flushed; this write must not land."""
    from app.db import get_db_session  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    app = FastAPI()
    app.add_middleware(DatabaseSessionMiddleware)

    @app.post("/flush")
    def flush() -> dict[str, bool]:
        db = get_db_session()
        db.add(_route_row())
        db.flush()
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/flush")

    assert response.status_code == 500
    assert "TENANT ISOLATION VIOLATION blocked: flush" in caplog.text
    assert _template_rows(engine) == 0


def test_an_insert_statement_into_the_template_is_refused(engine: Engine) -> None:
    from app.db import set_tenant_schema  # noqa: PLC0415
    from app.db.models import EhrRouteRow  # noqa: PLC0415
    from app.db.template_write_guard import (  # noqa: PLC0415
        TemplateSchemaWriteError,
        guard_template_writes,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as s:
        guard_template_writes(s)
        set_tenant_schema(s, "practice")
        with pytest.raises(TemplateSchemaWriteError):
            s.execute(insert(EhrRouteRow).values(id="guard-stmt", **_ROUTE, **_stamps()))
        s.rollback()
    assert _template_rows(engine) == 0


def test_a_platform_write_on_the_default_path_is_not_refused(engine: Engine) -> None:
    """Tables with an explicit schema land where they say, whatever the path."""
    from app.db import set_tenant_schema  # noqa: PLC0415
    from app.db.platform_models import EmailTenantMappingRow  # noqa: PLC0415
    from app.db.template_write_guard import guard_template_writes  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    email = f"guard-{uuid.uuid4().hex[:8]}@example.invalid"
    with OrmSession(engine) as s:
        guard_template_writes(s)
        set_tenant_schema(s, "practice")
        s.add(
            EmailTenantMappingRow(email=email, tenant_id="x", practice_id="x", created_at=utc_now())
        )
        s.flush()
        s.rollback()


def test_an_unguarded_session_may_build_the_template(engine: Engine) -> None:
    """Provisioning writes the template on purpose and never arms the guard."""
    from app.db import set_tenant_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as s:
        set_tenant_schema(s, "practice")
        s.add(_route_row())
        s.flush()
        s.rollback()


def test_a_schema_set_in_a_worker_thread_survives_a_mid_request_commit(engine: Engine) -> None:
    """A sync dependency sets the schema in one worker thread; the route runs in another.

    The ContextVar the pool listener reads does not cross threads, so after a
    commit hands the connection back, the next transaction must still get the
    schema from the session itself.
    """
    import threading  # noqa: PLC0415

    from app.db import DEFAULT_PRACTICE_OWN_SCHEMA, set_tenant_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as s:
        worker = threading.Thread(target=set_tenant_schema, args=(s, DEFAULT_PRACTICE_OWN_SCHEMA))
        worker.start()
        worker.join()
        assert _search_path(s).startswith(f"{DEFAULT_PRACTICE_OWN_SCHEMA},")

        s.commit()

        assert _search_path(s).startswith(f"{DEFAULT_PRACTICE_OWN_SCHEMA},")


def test_a_session_cleared_in_a_worker_thread_stays_off_the_template_after_a_commit(
    engine: Engine,
) -> None:
    """The request's ContextVar still names the template; the session was taken off it.

    After a commit hands the connection back, the next transaction must come
    back with no practice schema, not on the template the ContextVar names.
    """
    import threading  # noqa: PLC0415

    from app.db import (  # noqa: PLC0415
        DEFAULT_PRACTICE_SCHEMA,
        _current_tenant_schema,
        clear_tenant_schema,
        set_tenant_schema,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as s:
        set_tenant_schema(s, DEFAULT_PRACTICE_SCHEMA)
        worker = threading.Thread(target=clear_tenant_schema, args=(s,))
        worker.start()
        worker.join()
        assert _current_tenant_schema.get() == DEFAULT_PRACTICE_SCHEMA

        s.commit()

        assert _search_path(s) == "platform, public"
