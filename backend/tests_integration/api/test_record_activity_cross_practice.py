# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Record activity cannot reach across practices, through the real app + Postgres.

Two practices provisioned the production way (schemas from the template, RLS
on, platform rows with recorded owners) and a clinician signed in through the
real middleware, identity mapping and practice resolution. Only the identity
provider's token check is stood in for, since there is no provider here.

Practice B's owner is the owner of *a* practice, so the owner gate lets them
through. What has to stop them is the practice boundary:

* asking for practice A's client is a 404 that reads exactly like asking for
  a client that exists nowhere — a 403, or any difference at all, would tell
  B that the id is real;
* nothing is recorded as read, in either practice — a ``patient_activity_viewed``
  row naming A's client would itself be a record of a disclosure;
* B's owner listing B's own client never sees A's rows.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")


def _arm(conn: Connection, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


@contextmanager
def _practice_with_owner(engine: Engine, label: str) -> Iterator[SimpleNamespace]:
    """A provisioned practice, its owner signed up and recorded, and one client.

    The client has a few audit rows written by the owner, so there is
    activity to find — or, from the other practice, to fail to find.
    """
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
    schema = f"practice_test_activity_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    practice_id = f"activity-{label}-{uuid.uuid4().hex[:8]}"
    owner_id = str(uuid.uuid4())
    uid = f"fb{uuid.uuid4().hex[:26]}"
    email = f"activity-{owner_id[:8]}@example.invalid"
    now = utc_now()
    with Session(engine) as session:
        session.add(
            PlatformUserRow(
                id=owner_id,
                email=email,
                name=f"Owner {label}",
                created_at=now,
                status="approved",
                baa_accepted_at=now,
                baa_version="2024-01-01",
            )
        )
        session.flush()
        session.add(
            PracticeRow(
                id=practice_id,
                name=f"Activity {label}",
                schema_name=schema,
                owner_email=email,
                owner_user_id=owner_id,
                created_at=now,
            )
        )
        session.add(
            UserIdentityRow(provider="firebase", subject_id=uid, user_id=owner_id, linked_at=now)
        )
        session.add(
            EmailTenantMappingRow(
                email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
            )
        )
        session.commit()

    client_id = str(uuid.uuid4())
    row_ids: list[str] = []
    with engine.begin() as conn:
        _arm(conn, schema, owner_id)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower,"
                " last_name_lower, status, session_count, created_at, updated_at)"
                " VALUES (CAST(:pid AS uuid), 'Test', 'Client', 'test', 'client',"
                " 'active', 0, now(), now())"
            ),
            {"pid": client_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by)"
                " VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": client_id, "u": owner_id},
        )
        for minutes in (3, 2, 1):
            row_id = str(uuid.uuid4())
            conn.execute(
                text(
                    'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id,'
                    " actor_type, action, resource_type, resource_id, patient_id)"
                    " VALUES (CAST(:id AS uuid), :ts, :exp, :u, 'clinician',"
                    " 'patient_viewed', 'patient', :pid, CAST(:pid AS uuid))"
                ),
                {
                    "id": row_id,
                    "ts": datetime.now(UTC) - timedelta(minutes=minutes),
                    "exp": datetime.now(UTC) + timedelta(days=2555),
                    "u": owner_id,
                    "pid": client_id,
                },
            )
            row_ids.append(row_id)

    try:
        yield SimpleNamespace(
            id=practice_id,
            schema=schema,
            owner_id=owner_id,
            uid=uid,
            email=email,
            client_id=client_id,
            row_ids=row_ids,
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        with Session(engine) as session:
            session.query(EmailTenantMappingRow).filter_by(email=email).delete()
            session.query(UserIdentityRow).filter_by(user_id=owner_id).delete()
            session.query(PracticeRow).filter_by(id=practice_id).delete()
            session.query(PlatformUserRow).filter_by(id=owner_id).delete()
            session.commit()


@pytest.fixture(scope="module")
def practice_a(engine: Engine) -> Iterator[SimpleNamespace]:
    with _practice_with_owner(engine, "a") as practice:
        yield practice


@pytest.fixture(scope="module")
def practice_b(engine: Engine) -> Iterator[SimpleNamespace]:
    with _practice_with_owner(engine, "b") as practice:
        yield practice


def _get_as(app: FastAPI, owner: SimpleNamespace, path: str) -> tuple[int, Any]:
    """GET *path* as *owner*, through the real middleware and dependencies.

    Only the identity provider's signature check is replaced: the token is
    accepted as *owner*'s, with a second factor, and everything after that —
    identity mapping, practice resolution, search_path, RLS — is the app's.
    """
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    now = int(datetime.now(UTC).timestamp())
    identity = VerifiedIdentity(
        provider="firebase",
        subject_id=owner.uid,
        email=owner.email,
        mfa_satisfied=True,
        claims={
            "uid": owner.uid,
            "sub": owner.uid,
            "email": owner.email,
            "iat": now,
            "auth_time": now,
            "firebase": {"sign_in_provider": "password", "sign_in_second_factor": "totp"},
        },
    )
    with (
        patch("app.auth.service.verify_token", return_value=identity),
        TestClient(app) as client,
    ):
        response = client.get(path, headers={"Authorization": "Bearer test-token"})
    return response.status_code, response.json()


def _activity_views(engine: Engine, schema: str, reader: str, patient_id: str) -> int:
    """``patient_activity_viewed`` rows naming *patient_id* that *reader* can see.

    Read through the row policy as that practice's owner, who sees their
    own rows and every row about a client they can access.
    """
    with engine.begin() as conn:
        _arm(conn, schema, reader)
        count = conn.execute(
            text(
                "SELECT count(*) FROM audit_logs"
                " WHERE action = 'patient_activity_viewed'"
                " AND patient_id = CAST(:pid AS uuid)"
            ),
            {"pid": patient_id},
        ).scalar_one()
    return int(count)


def _all_rows(engine: Engine, schema: str, reader: str) -> int:
    with engine.begin() as conn:
        _arm(conn, schema, reader)
        count = conn.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()
    return int(count)


class TestAnotherPracticesClientIsNotThere:
    @pytest.mark.parametrize("with_cursor", [False, True])
    def test_it_reads_exactly_like_a_client_that_exists_nowhere(
        self,
        fastapi_app: FastAPI,
        practice_a: SimpleNamespace,
        practice_b: SimpleNamespace,
        with_cursor: bool,
    ) -> None:
        from app.models.audit import AuditCursor  # noqa: PLC0415

        nowhere = str(uuid.uuid4())
        query = ""
        if with_cursor:
            cursor = AuditCursor(timestamp=datetime.now(UTC), entry_id=practice_a.row_ids[0])
            query = f"?cursor={cursor.encode()}"

        status, body = _get_as(
            fastapi_app, practice_b, f"/api/patients/{practice_a.client_id}/activity{query}"
        )
        missing_status, missing_body = _get_as(
            fastapi_app, practice_b, f"/api/patients/{nowhere}/activity{query}"
        )

        assert status == 404
        # Indistinguishable from an id that exists nowhere, once the id the
        # caller supplied is set aside.
        assert str(body).replace(practice_a.client_id, "<id>") == str(missing_body).replace(
            nowhere, "<id>"
        )
        assert missing_status == 404
        flat = str(body)
        for secret in (practice_a.owner_id, practice_a.schema, practice_a.id, *practice_a.row_ids):
            assert secret not in flat

    def test_nothing_is_recorded_as_read_in_either_practice(
        self,
        engine: Engine,
        fastapi_app: FastAPI,
        practice_a: SimpleNamespace,
        practice_b: SimpleNamespace,
    ) -> None:
        a_before = _all_rows(engine, practice_a.schema, practice_a.owner_id)

        _get_as(fastapi_app, practice_b, f"/api/patients/{practice_a.client_id}/activity")

        assert (
            _activity_views(engine, practice_a.schema, practice_a.owner_id, practice_a.client_id)
            == 0
        )
        assert (
            _activity_views(engine, practice_b.schema, practice_b.owner_id, practice_a.client_id)
            == 0
        )
        assert _all_rows(engine, practice_a.schema, practice_a.owner_id) == a_before


class TestTheOwnersOwnClientShowsOnlyTheirPractice:
    def test_listing_their_own_client_never_returns_the_other_practices_rows(
        self,
        engine: Engine,
        fastapi_app: FastAPI,
        practice_a: SimpleNamespace,
        practice_b: SimpleNamespace,
    ) -> None:
        status, body = _get_as(
            fastapi_app, practice_b, f"/api/patients/{practice_b.client_id}/activity"
        )

        assert status == 200
        ids = {row["id"] for row in body["data"]}
        # Control: B's own rows are there, so an empty list cannot pass.
        assert set(practice_b.row_ids) <= ids
        assert not ids & set(practice_a.row_ids)
        assert (
            _activity_views(engine, practice_b.schema, practice_b.owner_id, practice_b.client_id)
            >= 1
        )
