# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end DPoP enforcement against the real app + real Postgres.

The unit suite at ``backend/tests/test_dpop_middleware.py`` injects an
in-memory device registry, so it never exercises the pieces most likely
to break in production:

* the enrollment write path (``CompanionDeviceService.enroll``) storing
  a JWK in ``platform.companion_devices`` and the middleware reading it
  back through JSONB — a serialization round-trip the fakes skip,
* the real ``app.main`` middleware stack and registration order
  (DPoP runs inside ``DatabaseSessionMiddleware`` so the request-scoped
  session is available for the device lookup),
* ``last_seen`` persistence through the real repository.

What stays fake: Firebase identity resolution only
(``_resolve_user_id`` is patched to return the enrolled user). Token
verification is generic auth machinery with its own coverage; the
DPoP-specific surface — device binding, revocation, cross-user
rejection, signature verification, htu/iat/jti checks — all runs real
here, with proofs signed by a locally generated P-256 key.

``ENABLE_DPOP_VALIDATION=true`` is set at module import (before
``app.main`` is first imported anywhere in the session) so the
middleware enforces. Requests without ``X-Install-ID`` are unaffected,
so other e2e modules sharing the process see no behavior change.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import TYPE_CHECKING

import jwt
import pytest
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
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

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("ENABLE_DPOP_VALIDATION", "true")

# Any route works for probing the middleware verdict — DPoP dispatch runs
# before routing. /api/health needs no auth deps, so a passing proof is a
# clean 200 and a failing one is the middleware's 401.
_PROBE_PATH = "/api/health"


def _make_keypair() -> tuple[EllipticCurvePrivateKey, dict[str, str]]:
    """Generate a P-256 keypair and its public JWK (what a companion enrolls)."""
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    return private_key, public_jwk


def _sign_proof(
    private_key: EllipticCurvePrivateKey,
    *,
    htm: str = "GET",
    htu: str = f"http://testserver{_PROBE_PATH}",
    iat: float | None = None,
    jti: str | None = None,
) -> str:
    return jwt.encode(
        {
            "htm": htm,
            "htu": htu,
            "iat": int(iat if iat is not None else time.time()),
            "jti": jti or uuid.uuid4().hex,
        },
        private_key,
        algorithm="ES256",
        headers={"typ": "dpop+jwt"},
    )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    from pathlib import Path  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def enrolled(engine: Engine) -> Iterator[dict]:
    """Enroll a device through the real service, against the real DB.

    Returns the user id, install_id, signing key, and a second
    ("other") user + key for cross-user / wrong-key cases.
    """
    from app.db.platform_models import PlatformUserRow  # noqa: PLC0415
    from app.models.companion_device import CompanionEnrollment  # noqa: PLC0415
    from app.repositories.postgres.companion_device import (  # noqa: PLC0415
        PostgresCompanionDeviceRepository,
    )
    from app.services.companion_device_service import (  # noqa: PLC0415
        CompanionDeviceService,
    )
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    user_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    install_id = uuid.uuid4().hex
    other_install_id = uuid.uuid4().hex
    private_key, public_jwk = _make_keypair()
    other_key, other_jwk = _make_keypair()

    with Session(engine) as session:
        for uid, label in ((user_id, "a"), (other_user_id, "b")):
            session.add(
                PlatformUserRow(
                    id=uid,
                    email=f"dpop-e2e-{label}-{uid[:8]}@example.com",
                    name="DPoP E2E",
                    created_at=utc_now(),
                )
            )
        session.flush()
        service = CompanionDeviceService(PostgresCompanionDeviceRepository(session))
        service.enroll(
            user_id,
            CompanionEnrollment(
                install_id=install_id,
                platform="mac",
                os_version="14.5",
                hostname_hash="a" * 64,
                device_public_key_jwk=public_jwk,
                key_storage="hardware",
            ),
        )
        service.enroll(
            other_user_id,
            CompanionEnrollment(
                install_id=other_install_id,
                platform="windows",
                os_version="10.0.22631",
                hostname_hash="b" * 64,
                device_public_key_jwk=other_jwk,
                key_storage="software",
            ),
        )
        session.commit()

    yield {
        "user_id": user_id,
        "install_id": install_id,
        "key": private_key,
        "other_user_id": other_user_id,
        "other_install_id": other_install_id,
        "other_key": other_key,
    }

    with Session(engine) as session:
        session.execute(
            text("DELETE FROM platform.users WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": user_id, "b": other_user_id},
        )
        session.commit()


@pytest.fixture
def client(enrolled: dict, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Real ``app.main`` app; only Firebase identity resolution is patched."""
    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    monkeypatch.setattr(
        "app.middleware.dpop._resolve_user_id",
        lambda _request, _token: enrolled["user_id"],
    )
    return TestClient(app)


def _headers(install_id: str, proof: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer e2e-token",
        "X-Install-ID": install_id,
        "DPoP": proof,
    }


class TestDPoPEndToEnd:
    def test_valid_proof_passes_and_touches_last_seen(
        self, client: TestClient, enrolled: dict, engine: Engine
    ) -> None:
        before = self._last_seen(engine, enrolled["install_id"])
        proof = _sign_proof(enrolled["key"])
        resp = client.get(_PROBE_PATH, headers=_headers(enrolled["install_id"], proof))
        assert resp.status_code == 200
        after = self._last_seen(engine, enrolled["install_id"])
        assert after > before

    def test_replayed_jti_rejected(self, client: TestClient, enrolled: dict) -> None:
        proof = _sign_proof(enrolled["key"])
        headers = _headers(enrolled["install_id"], proof)
        assert client.get(_PROBE_PATH, headers=headers).status_code == 200
        replay = client.get(_PROBE_PATH, headers=headers)
        assert replay.status_code == 401
        assert replay.headers["WWW-Authenticate"] == 'DPoP error="invalid_proof"'

    def test_proof_signed_by_wrong_key_rejected(self, client: TestClient, enrolled: dict) -> None:
        proof = _sign_proof(enrolled["other_key"])
        resp = client.get(_PROBE_PATH, headers=_headers(enrolled["install_id"], proof))
        assert resp.status_code == 401

    def test_cross_user_install_id_rejected(self, client: TestClient, enrolled: dict) -> None:
        # The caller resolves to user A but presents user B's install_id
        # with a proof correctly signed by B's key. Binding must reject.
        proof = _sign_proof(enrolled["other_key"])
        resp = client.get(_PROBE_PATH, headers=_headers(enrolled["other_install_id"], proof))
        assert resp.status_code == 401

    def test_revoked_device_rejected(
        self, client: TestClient, enrolled: dict, engine: Engine
    ) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE platform.companion_devices SET revoked_at = now() "
                    "WHERE install_id = :iid"
                ),
                {"iid": enrolled["install_id"]},
            )
        try:
            proof = _sign_proof(enrolled["key"])
            resp = client.get(_PROBE_PATH, headers=_headers(enrolled["install_id"], proof))
            assert resp.status_code == 401
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE platform.companion_devices SET revoked_at = NULL "
                        "WHERE install_id = :iid"
                    ),
                    {"iid": enrolled["install_id"]},
                )

    def test_htu_for_other_host_rejected(self, client: TestClient, enrolled: dict) -> None:
        # A proof signed for one host must not verify on a request that
        # arrives at another (no cross-deployment replay).
        proof = _sign_proof(enrolled["key"], htu=f"https://dev.pablo.health{_PROBE_PATH}")
        resp = client.get(_PROBE_PATH, headers=_headers(enrolled["install_id"], proof))
        assert resp.status_code == 401

    def test_forwarded_host_canonicalization(self, client: TestClient, enrolled: dict) -> None:
        # Behind the LB the externally-signed URL arrives via
        # X-Forwarded-Proto/Host; the proof signs the external form. The
        # middleware only honors a forwarded host that is in its trusted
        # set (derived from app_url), so we forward the CONFIGURED public
        # host — whatever APP_URL resolves to in this test environment —
        # to exercise the trusted path.
        from urllib.parse import urlsplit  # noqa: PLC0415

        from app.settings import settings  # noqa: PLC0415

        trusted_host = urlsplit(settings.app_url).netloc
        assert trusted_host, "APP_URL must resolve to a host for this test"
        proof = _sign_proof(enrolled["key"], htu=f"https://{trusted_host}{_PROBE_PATH}")
        headers = _headers(enrolled["install_id"], proof)
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = trusted_host
        resp = client.get(_PROBE_PATH, headers=headers)
        assert resp.status_code == 200

    def test_spoofed_forwarded_host_rejected(self, client: TestClient, enrolled: dict) -> None:
        # An untrusted X-Forwarded-Host is client-controlled and must not
        # shift the htu comparison: the middleware falls back to the raw
        # request host, so a proof signed for the spoofed host is rejected.
        proof = _sign_proof(enrolled["key"], htu=f"https://evil.example{_PROBE_PATH}")
        headers = _headers(enrolled["install_id"], proof)
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = "evil.example"
        resp = client.get(_PROBE_PATH, headers=headers)
        assert resp.status_code == 401
        assert resp.headers["WWW-Authenticate"] == 'DPoP error="invalid_proof"'

    def test_stale_iat_rejected(self, client: TestClient, enrolled: dict) -> None:
        proof = _sign_proof(enrolled["key"], iat=time.time() - 120)
        resp = client.get(_PROBE_PATH, headers=_headers(enrolled["install_id"], proof))
        assert resp.status_code == 401

    def test_unknown_install_id_rejected(self, client: TestClient, enrolled: dict) -> None:
        proof = _sign_proof(enrolled["key"])
        resp = client.get(_PROBE_PATH, headers=_headers(uuid.uuid4().hex, proof))
        assert resp.status_code == 401

    def test_no_install_id_passes_as_legacy(self, client: TestClient) -> None:
        resp = client.get(_PROBE_PATH, headers={"Authorization": "Bearer e2e-token"})
        assert resp.status_code == 200

    @staticmethod
    def _last_seen(engine: Engine, install_id: str):  # type: ignore[no-untyped-def]
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT last_seen FROM platform.companion_devices WHERE install_id = :iid"),
                {"iid": install_id},
            ).scalar_one()
