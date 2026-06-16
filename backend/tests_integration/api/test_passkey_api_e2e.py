# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end passkey ceremonies against the real app + real Postgres (PABLO-egm.6).

Drives the four WebAuthn endpoints —
``/api/auth/passkey/{register,authenticate}/{begin,finish}`` — through
``TestClient`` against a testcontainers Postgres, answering each ``begin`` with a
**real** ES256 assertion from an in-process software authenticator
(``_soft_webauthn``). The crypto, the single-use challenge store
(``platform.passkey_challenges``), the credential repository
(``platform.passkey_credentials``), and the ``pablo_amr`` claim mint are all
exercised for real. The only seam stubbed is the Firebase custom-token call
itself — the design (``docs/internal/passkey-auth-e2e-design.md``) deliberately
keeps the assertion-verify + claim-stamp coverage here and reserves the
``signInWithCustomToken`` round-trip for the browser e2e (PABLO-egm.7).

The unit suite at ``backend/tests/test_passkey_service.py`` monkeypatches
``verify_authentication_response``, so it proves the service's *decision* logic
but nothing about real WebAuthn verification, JSON/CBOR round-trips, the DB
single-use guarantee, or counter persistence. This file proves exactly those.

What this covers (the enforcement matrix from the design doc):
  * register begin→finish with a valid attestation → 201 + a credential row
  * authenticate begin→finish with a valid assertion → 200 + a minted token
    carrying ``pablo_amr: ["webauthn"]``; the DB sign counter advances
  * the minted claim satisfies the real ``require_mfa`` seam; a first-factor
    -only token does not (closing the loop to ``FirebaseVerifier``)
  * single-use challenge: a replayed register/authenticate challenge → 400
  * an unknown / never-issued authentication challenge → 400
  * a cloned authenticator (sign counter not advancing) → 401
  * an assertion against a revoked credential → 401
  * UV not performed when user-verification is required → 401
  * an attestation signed for the wrong origin → 400
  * adding a second passkey from a first-factor session → 403 MFA_REQUIRED

Out of scope here (named so green is never read as total): the native/desktop
code-exchange gate and the pure-function factor checks live in
``backend/tests/test_passkey_enforcement.py``; the browser round-trip +
``signInWithCustomToken`` is PABLO-egm.7.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import create_engine, text

from ._soft_webauthn import SoftWebAuthnAuthenticator

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from httpx import Response
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

# Must match settings.webauthn_rp_id / webauthn_origins defaults (localhost /
# http://localhost:3000). WebAuthn is origin-bound: an assertion signed for any
# other origin fails closed, which is the point of the wrong-origin test.
_RP_ID = "localhost"
_ORIGIN = "http://localhost:3000"

_BASE = "/api/auth/passkey"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """Engine bound to the integration Postgres with alembic head applied.

    ``platform.passkey_credentials`` / ``platform.passkey_challenges`` and the
    ``user_id`` FK to ``platform.users`` come from migration
    ``9f4c1a7b2e60`` — run alembic explicitly rather than relying on
    ``Base.metadata.create_all``.
    """
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
def fastapi_app() -> FastAPI:
    """Import the real app lazily, after the Postgres gate."""
    from app.main import app  # noqa: PLC0415

    return app


@pytest.fixture
def seed_user(engine: Engine) -> Iterator[dict[str, str]]:
    """A platform user + linked Firebase identity the passkey FKs require.

    ``authenticate/finish`` resolves the user's Firebase uid via the identity
    repository before minting, so the link must exist for the assertion to mint
    a token.
    """
    from app.db.platform_models import PlatformUserRow, UserIdentityRow  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    user_id = str(uuid.uuid4())
    firebase_uid = f"fb-{uuid.uuid4().hex[:24]}"
    email = f"passkey-e2e-{user_id[:8]}@example.com"

    with Session(engine) as session:
        session.add(
            PlatformUserRow(id=user_id, email=email, name="Passkey E2E", created_at=utc_now())
        )
        session.flush()
        session.add(
            UserIdentityRow(
                provider="firebase",
                subject_id=firebase_uid,
                user_id=user_id,
                linked_at=utc_now(),
            )
        )
        session.commit()

    yield {"user_id": user_id, "firebase_uid": firebase_uid, "email": email}

    with Session(engine) as session:
        session.execute(
            text("DELETE FROM platform.user_identities WHERE user_id = CAST(:id AS uuid)"),
            {"id": user_id},
        )
        session.execute(
            text("DELETE FROM platform.users WHERE id = CAST(:id AS uuid)"),
            {"id": user_id},
        )
        session.commit()


@pytest.fixture(autouse=True)
def _clean_passkey_tables(engine: Engine) -> None:
    """Reset the shared platform passkey tables so each test is independent."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE platform.passkey_credentials, "
                "platform.passkey_challenges RESTART IDENTITY"
            )
        )


@pytest.fixture
def harness(
    fastapi_app: FastAPI,
    seed_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    """A TestClient with the enrolling user wired in and the mint captured.

    Overrides:
      * ``get_current_user_no_mfa`` → the seeded user (no Firebase round-trip).
        It deliberately does NOT stash a ``verified_identity``, so the session
        reads as first-factor-only — exactly the posture a first enrollment runs
        under, and what the "second passkey needs MFA" gate must reject.
      * ``require_rate_limit`` → no-op (the preauth IP limiter is unrelated to
        the ceremony logic under test).
    Firebase mint is stubbed at the service module: the captured uid + claims
    are exposed so a test can assert ``pablo_amr`` is stamped only after a
    verified assertion.
    """
    from app.auth.service import get_current_user_no_mfa  # noqa: PLC0415
    from app.models.user import User  # noqa: PLC0415
    from app.rate_limit import require_rate_limit  # noqa: PLC0415
    from app.services import passkey_service as svc  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    user = User(
        id=seed_user["user_id"],
        email=seed_user["email"],
        name="Passkey E2E",
        created_at=utc_now(),
    )
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: user
    fastapi_app.dependency_overrides[require_rate_limit] = lambda: None

    captured: dict[str, Any] = {}

    def _mint(firebase_uid: str, claims: dict[str, Any]) -> bytes:
        captured["uid"] = firebase_uid
        captured["claims"] = claims
        return b"custom-token-stub"

    monkeypatch.setattr(svc, "initialize_firebase_app", lambda: None)
    monkeypatch.setattr(svc.firebase_auth, "create_custom_token", _mint)

    try:
        yield SimpleNamespace(client=TestClient(fastapi_app), user=user, mint=captured)
    finally:
        fastapi_app.dependency_overrides.clear()


# --- ceremony helpers -------------------------------------------------------


def _register(client: TestClient, authenticator: SoftWebAuthnAuthenticator) -> dict[str, Any]:
    """Run a full register begin→finish; assert 201 and return the body."""
    options = client.post(f"{_BASE}/register/begin").json()
    credential = authenticator.create(options, origin=_ORIGIN)
    resp = client.post(
        f"{_BASE}/register/finish",
        json={"credential": credential, "device_label": "Test Key"},
    )
    assert resp.status_code == 201, f"register/finish: {resp.status_code} {resp.text}"
    return resp.json()


def _authenticate(
    client: TestClient,
    authenticator: SoftWebAuthnAuthenticator,
    **get_kwargs: Any,
) -> Response:
    """Run authenticate begin→finish; return the raw finish response."""
    options = client.post(f"{_BASE}/authenticate/begin", json={}).json()
    assertion = authenticator.get(options, origin=_ORIGIN, **get_kwargs)
    return client.post(f"{_BASE}/authenticate/finish", json={"credential": assertion})


# --- registration -----------------------------------------------------------


class TestRegistration:
    def test_valid_attestation_persists_credential(
        self, harness: SimpleNamespace, engine: Engine, seed_user: dict[str, str]
    ) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        body = _register(harness.client, authenticator)

        assert body["credential_id"] == authenticator.credential_id_b64
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT user_id, sign_count, device_label, revoked_at "
                        "FROM platform.passkey_credentials WHERE credential_id = :cid"
                    ),
                    {"cid": authenticator.credential_id_b64},
                )
                .mappings()
                .one_or_none()
            )
        assert row is not None, "credential row not written"
        assert str(row["user_id"]) == seed_user["user_id"]
        assert row["device_label"] == "Test Key"
        assert row["revoked_at"] is None

    def test_replayed_registration_challenge_rejected(self, harness: SimpleNamespace) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        options = harness.client.post(f"{_BASE}/register/begin").json()
        credential = authenticator.create(options, origin=_ORIGIN)

        first = harness.client.post(f"{_BASE}/register/finish", json={"credential": credential})
        assert first.status_code == 201, first.text

        # Same signed attestation again: the challenge is already consumed.
        replay = harness.client.post(f"{_BASE}/register/finish", json={"credential": credential})
        assert replay.status_code == 400, replay.text

    def test_wrong_origin_attestation_rejected(self, harness: SimpleNamespace) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        options = harness.client.post(f"{_BASE}/register/begin").json()
        credential = authenticator.create(options, origin="http://evil.example")

        resp = harness.client.post(f"{_BASE}/register/finish", json={"credential": credential})
        assert resp.status_code == 400, resp.text

    def test_second_passkey_from_first_factor_session_rejected(
        self, harness: SimpleNamespace
    ) -> None:
        # First passkey enrolls fine from a first-factor session.
        _register(harness.client, SoftWebAuthnAuthenticator())

        # A second begin from the same (still first-factor-only) session is
        # refused — a phished password must not silently add an authenticator.
        resp = harness.client.post(f"{_BASE}/register/begin")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "MFA_REQUIRED"


# --- authentication ---------------------------------------------------------


class TestAuthentication:
    def test_valid_assertion_mints_factor_token(
        self, harness: SimpleNamespace, engine: Engine, seed_user: dict[str, str]
    ) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        resp = _authenticate(harness.client, authenticator)
        assert resp.status_code == 200, resp.text
        assert resp.json()["custom_token"] == "custom-token-stub"  # noqa: S105 — stub mint, not a secret

        # The factor claim is stamped only here, after a verified assertion,
        # and against the user's resolved Firebase uid (the H1 guard).
        assert harness.mint["uid"] == seed_user["firebase_uid"]
        # The mint also records the asserting credential's provenance: the soft
        # authenticator is device-bound (hw) and, with no trust store, unattested.
        assert harness.mint["claims"] == {
            "pablo_amr": ["webauthn"],
            "pablo_passkey": {"hw": True, "att": False},
        }

        # The DB sign counter advanced (0 at enrollment → 1 after assertion).
        with engine.connect() as conn:
            sign_count = conn.execute(
                text(
                    "SELECT sign_count FROM platform.passkey_credentials WHERE credential_id = :cid"
                ),
                {"cid": authenticator.credential_id_b64},
            ).scalar_one()
        assert sign_count == 1

    def test_minted_claim_satisfies_mfa_seam(self, harness: SimpleNamespace) -> None:
        """The produced ``pablo_amr`` claim makes the real verifier MFA-satisfied.

        Closes the loop end-to-end: a real assertion → minted claim → the same
        ``FirebaseVerifier`` production uses → ``require_mfa`` passes; a
        first-factor-only token does not.
        """
        from app.auth.providers import FirebaseVerifier  # noqa: PLC0415

        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)
        assert _authenticate(harness.client, authenticator).status_code == 200

        minted = FirebaseVerifier().verify_from_decoded(
            {"uid": harness.mint["uid"], "email": "t@example.com", **harness.mint["claims"]}
        )
        assert minted.mfa_satisfied is True

        first_factor_only = FirebaseVerifier().verify_from_decoded(
            {"uid": "fb-uid", "email": "t@example.com"}
        )
        assert first_factor_only.mfa_satisfied is False

    def test_replayed_authentication_challenge_rejected(self, harness: SimpleNamespace) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        options = harness.client.post(f"{_BASE}/authenticate/begin", json={}).json()
        assertion = authenticator.get(options, origin=_ORIGIN)

        first = harness.client.post(f"{_BASE}/authenticate/finish", json={"credential": assertion})
        assert first.status_code == 200, first.text

        replay = harness.client.post(f"{_BASE}/authenticate/finish", json={"credential": assertion})
        assert replay.status_code == 400, replay.text

    def test_unknown_challenge_rejected(self, harness: SimpleNamespace) -> None:
        from webauthn.helpers import bytes_to_base64url  # noqa: PLC0415

        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        # An assertion whose challenge the server never issued: consume() finds
        # no pending row, so finish rejects before any signature check.
        forged_options = {
            "challenge": bytes_to_base64url(os.urandom(32)),
            "rpId": _RP_ID,
        }
        assertion = authenticator.get(forged_options, origin=_ORIGIN)
        resp = harness.client.post(f"{_BASE}/authenticate/finish", json={"credential": assertion})
        assert resp.status_code == 400, resp.text

    def test_cloned_authenticator_rejected(self, harness: SimpleNamespace) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        # One good assertion advances the stored counter to 1.
        assert _authenticate(harness.client, authenticator).status_code == 200

        # A later assertion that does NOT advance the counter signals a clone.
        resp = _authenticate(harness.client, authenticator, sign_count=1)
        assert resp.status_code == 401, resp.text

    def test_revoked_credential_rejected(self, harness: SimpleNamespace, engine: Engine) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE platform.passkey_credentials SET revoked_at = now() "
                    "WHERE credential_id = :cid"
                ),
                {"cid": authenticator.credential_id_b64},
            )

        resp = _authenticate(harness.client, authenticator)
        assert resp.status_code == 401, resp.text

    def test_user_verification_not_performed_rejected(self, harness: SimpleNamespace) -> None:
        authenticator = SoftWebAuthnAuthenticator()
        _register(harness.client, authenticator)

        # Enrolled with UV, but this assertion clears the UV flag — the server
        # requires user verification, so verification fails. This is what makes
        # a passkey a *second factor*, not mere possession. An assertion that
        # fails verification surfaces as 401 (vs. 400 for a registration that
        # fails verification — see the wrong-origin test above).
        resp = _authenticate(harness.client, authenticator, user_verified=False)
        assert resp.status_code == 401, resp.text
