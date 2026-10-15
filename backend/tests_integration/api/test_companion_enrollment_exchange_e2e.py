# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The native code exchange reports how companion enrollment went.

Runs the real ``/api/auth/native/exchange`` route against real Postgres:
identity resolution, the device service and ``platform.companion_devices``
are all real. Only the one-time code is minted directly through the code
store, which skips the Firebase token check on ``/native/code``.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

import jwt
import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_REDIRECT_URI = "pablohealth://callback"
_ISSUED_ID = "id_tok"
_ISSUED_REFRESH = "ref_tok"


def _public_jwk() -> dict[str, str]:
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    jwk: dict[str, str] = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    return jwk


def _enrollment(install_id: str) -> dict[str, Any]:
    return {
        "install_id": install_id,
        "platform": "mac",
        "os_version": "15.2",
        "hostname_hash": "c" * 64,
        "device_public_key_jwk": _public_jwk(),
        "key_storage": "hardware",
    }


@pytest.fixture(scope="module")
def users(engine: Engine) -> Iterator[dict[str, str]]:
    """Two signed-up users, each linked to a Firebase uid."""
    from app.db.platform_models import PlatformUserRow, UserIdentityRow  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    ids = {label: str(uuid.uuid4()) for label in ("a", "b")}
    uids = {label: f"fb-enroll-{label}-{uuid.uuid4().hex[:12]}" for label in ids}
    with Session(engine) as session:
        for label, user_id in ids.items():
            session.add(
                PlatformUserRow(
                    id=user_id,
                    email=f"enroll-e2e-{label}-{user_id[:8]}@example.com",
                    name="Enrollment E2E",
                    created_at=utc_now(),
                )
            )
        session.flush()
        for label, user_id in ids.items():
            session.add(
                UserIdentityRow(
                    provider="firebase",
                    subject_id=uids[label],
                    user_id=user_id,
                    linked_at=utc_now(),
                )
            )
        session.commit()

    yield {"a_id": ids["a"], "a_uid": uids["a"], "b_id": ids["b"], "b_uid": uids["b"]}

    with Session(engine) as session:
        session.execute(
            text("DELETE FROM platform.users WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": ids["a"], "b": ids["b"]},
        )
        session.commit()


@pytest.fixture
def client() -> TestClient:
    from app.main import app  # noqa: PLC0415
    from app.rate_limit import reset_preauth_limiter  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    reset_preauth_limiter()
    return TestClient(app)


def _exchange(client: TestClient, firebase_uid: str, enrollment: dict[str, Any]) -> Any:
    from app.services.auth_code_store import create_auth_code  # noqa: PLC0415

    code = create_auth_code(
        id_token=_ISSUED_ID,
        refresh_token=_ISSUED_REFRESH,
        redirect_uri=_REDIRECT_URI,
        firebase_uid=firebase_uid,
    )
    return client.post(
        "/api/auth/native/exchange",
        json={"code": code, "redirect_uri": _REDIRECT_URI, "enrollment": enrollment},
    )


def _device_owners(engine: Engine, install_id: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT user_id FROM platform.companion_devices WHERE install_id = :i"),
            {"i": install_id},
        ).all()
    return {str(row[0]) for row in rows}


class TestEnrollmentOutcome:
    def test_successful_enrollment_reports_enrolled(
        self, client: TestClient, engine: Engine, users: dict[str, str]
    ) -> None:
        install_id = uuid.uuid4().hex
        resp = _exchange(client, users["a_uid"], _enrollment(install_id))

        assert resp.status_code == 200
        assert resp.json()["enrollment"] == "enrolled"
        assert _device_owners(engine, install_id) == {users["a_id"]}

    def test_second_user_on_the_same_install_is_enrolled_too(
        self, client: TestClient, engine: Engine, users: dict[str, str]
    ) -> None:
        # The companion keeps one install_id + key per OS user whichever
        # account signs in, so a second account must get its own row.
        install_id = uuid.uuid4().hex
        assert _exchange(client, users["a_uid"], _enrollment(install_id)).json()["enrollment"] == (
            "enrolled"
        )

        resp = _exchange(client, users["b_uid"], _enrollment(install_id))

        assert resp.status_code == 200
        body = resp.json()
        assert body["enrollment"] == "enrolled"
        assert body["id_token"] == _ISSUED_ID
        assert _device_owners(engine, install_id) == {users["a_id"], users["b_id"]}

    def test_same_user_reenrolling_keeps_one_row(
        self, client: TestClient, engine: Engine, users: dict[str, str]
    ) -> None:
        install_id = uuid.uuid4().hex
        for _ in range(2):
            resp = _exchange(client, users["a_uid"], _enrollment(install_id))
            assert resp.json()["enrollment"] == "enrolled"
        assert _device_owners(engine, install_id) == {users["a_id"]}

    def test_revoked_enrollment_reports_failed_and_stays_revoked(
        self, client: TestClient, engine: Engine, users: dict[str, str]
    ) -> None:
        install_id = uuid.uuid4().hex
        assert _exchange(client, users["a_uid"], _enrollment(install_id)).json()["enrollment"] == (
            "enrolled"
        )
        select_row = text(
            "SELECT device_public_key_jwk, revoked_at FROM platform.companion_devices "
            "WHERE install_id = :i AND user_id = CAST(:u AS uuid)"
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE platform.companion_devices SET revoked_at = now() "
                    "WHERE install_id = :i AND user_id = CAST(:u AS uuid)"
                ),
                {"i": install_id, "u": users["a_id"]},
            )
            before = conn.execute(select_row, {"i": install_id, "u": users["a_id"]}).one()

        resp = _exchange(client, users["a_uid"], _enrollment(install_id))
        assert resp.status_code == 200
        assert resp.json()["enrollment"] == "failed"
        assert resp.json()["id_token"] == _ISSUED_ID
        with engine.connect() as conn:
            after = conn.execute(select_row, {"i": install_id, "u": users["a_id"]}).one()
        assert after == before

        # Another account on the same install is unaffected by A's revocation.
        assert _exchange(client, users["b_uid"], _enrollment(install_id)).json()["enrollment"] == (
            "enrolled"
        )

    def test_retry_after_failure_enrolls(
        self, client: TestClient, engine: Engine, users: dict[str, str]
    ) -> None:
        # A rejected key on one attempt must not poison the next one: the
        # companion retries by signing in again with a good payload.
        install_id = uuid.uuid4().hex
        bad = {**_enrollment(install_id), "device_public_key_jwk": {"kty": "EC", "crv": "P-256"}}
        assert _exchange(client, users["b_uid"], bad).json()["enrollment"] == "failed"
        assert _device_owners(engine, install_id) == set()

        resp = _exchange(client, users["b_uid"], _enrollment(install_id))

        assert resp.json()["enrollment"] == "enrolled"
        assert _device_owners(engine, install_id) == {users["b_id"]}
