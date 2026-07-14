# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration-style tests for the DPoP proof-validation middleware.

Exercises the full ``DPoPMiddleware.dispatch`` path through a Starlette
``TestClient`` for the matrix in
``docs/design/companion-dpop-binding.md`` § "Test enforcement":

- valid JWT + valid DPoP + valid install_id → 200
- valid JWT + no install_id → 200 (legacy pass)
- valid JWT + valid install_id + bad proof signature → 401
- valid JWT + valid install_id + proof for a different URL → 401
- valid JWT + valid install_id + stale iat → 401
- valid JWT + valid install_id + replayed jti → 401
- valid JWT + revoked install_id → 401
- valid JWT + unknown install_id → 401
- flag off → hard no-op pass-through (no header inspection)

The middleware's user resolution (``_resolve_user_id``) is patched to a
fixed user id so the tests don't need Firebase or a live identity table —
the resolution path is the same one the auth dependencies already cover.
The device registry and ``last_seen`` touch are injected into the
middleware via its constructor (an in-memory fake), so no Postgres is
required. At least one route per top-level prefix is mounted so the
global middleware is exercised across prefixes, not a single endpoint.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import app.middleware.dpop as dpop_module
import jwt
import pytest
from app.middleware.dpop import (
    DPOP_HEADER,
    INSTALL_ID_HEADER,
    WWW_AUTHENTICATE_VALUE,
    DPoPMiddleware,
)
from app.models.companion_device import CompanionDevice
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

TEST_USER_ID = "pablo-user-001"
INSTALL_ID = "install-abc-123"
# Routes spanning several top-level prefixes so the global middleware is
# proven across prefixes (not just one endpoint).
ROUTES = ("/api/patients/p1", "/api/sessions/s1", "/api/users/me/devices")


# --------------------------------------------------------------------------- #
# Key + proof helpers
# --------------------------------------------------------------------------- #
def _make_keypair() -> tuple[dict[str, str], object]:
    """Return (public JWK dict, signing key) for a fresh P-256 key."""
    priv = ec.generate_private_key(ec.SECP256R1())
    public_jwk = json.loads(ECAlgorithm.to_jwk(priv.public_key()))
    private_jwk = json.loads(ECAlgorithm.to_jwk(priv))
    signing_key = jwt.PyJWK.from_dict(private_jwk).key
    return public_jwk, signing_key


def _make_device(
    public_jwk: dict[str, str],
    *,
    user_id: str = TEST_USER_ID,
    install_id: str = INSTALL_ID,
    revoked: bool = False,
) -> CompanionDevice:
    now = datetime.now(UTC)
    return CompanionDevice(
        install_id=install_id,
        user_id=user_id,
        device_public_key_jwk=public_jwk,
        jkt="x" * 43,
        key_storage="hardware",
        platform="mac",
        os_version="14.5",
        hostname_hash=None,
        enrolled_at=now,
        last_seen=now,
        revoked_at=now if revoked else None,
    )


def _sign_proof(
    signing_key: object,
    *,
    htm: str,
    htu: str,
    iat: int | None = None,
    jti: str | None = None,
) -> str:
    return jwt.encode(
        {
            "htm": htm,
            "htu": htu,
            "iat": int(time.time()) if iat is None else iat,
            "jti": jti or str(uuid.uuid4()),
        },
        signing_key,
        algorithm="ES256",
        headers={"typ": "dpop+jwt"},
    )


# --------------------------------------------------------------------------- #
# Test app factory
# --------------------------------------------------------------------------- #
class _FakeSettings:
    def __init__(
        self,
        enable: bool,
        *,
        backend_base_url: str = "",
        app_url: str = "http://testserver",
        dpop_trusted_hosts: str = "",
    ) -> None:
        self.enable_dpop_validation = enable
        # The trusted-host derivation reads these three fields. Default the
        # app host to the TestClient's host so the standard cases (which
        # don't set X-Forwarded-Host) and a forwarded-but-trusted case
        # both line up with the real request host. backend_base_url
        # defaults empty (contributing nothing) so existing cases keep
        # asserting purely on the app_url-derived host.
        self.backend_base_url = backend_base_url
        self.app_url = app_url
        self.dpop_trusted_hosts = dpop_trusted_hosts


def _build_app(
    *,
    enable: bool,
    devices: dict[str, CompanionDevice],
    resolve_user_id: str | None = TEST_USER_ID,
    touched: list[str] | None = None,
    backend_base_url: str = "",
    app_url: str = "http://testserver",
    dpop_trusted_hosts: str = "",
):
    """Build a minimal app wired with the DPoP middleware.

    ``devices`` is the in-memory registry keyed by install_id; the
    middleware's ``device_lookup`` reads from it. ``resolve_user_id`` is
    the user id the (patched) token resolver returns — ``None`` simulates
    an unverifiable token.
    """
    app = FastAPI()

    for path in ROUTES:
        # Closure binds the literal path; handler is a stand-in for any
        # authenticated route the global middleware would wrap.
        app.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
        app.add_api_route(path, lambda: {"ok": True}, methods=["POST"])

    touch_sink = touched if touched is not None else []

    app.add_middleware(
        DPoPMiddleware,
        settings=_FakeSettings(
            enable,
            backend_base_url=backend_base_url,
            app_url=app_url,
            dpop_trusted_hosts=dpop_trusted_hosts,
        ),
        device_lookup=devices.get,
        touch=touch_sink.append,
    )

    # Patch the user resolver the middleware calls. We patch the symbol on
    # the dpop module so the middleware's ``_resolve_user_id(...)`` call
    # resolves to our stub regardless of token/Firebase availability.
    def _stub_resolve(_request, _token):
        return resolve_user_id

    dpop_module._resolve_user_id = _stub_resolve  # type: ignore[assignment]

    return TestClient(app), touch_sink


@pytest.fixture
def keypair() -> tuple[dict[str, str], object]:
    return _make_keypair()


@pytest.fixture(autouse=True)
def _restore_resolver():
    """Restore the real ``_resolve_user_id`` after each test patches it."""
    original = dpop_module._resolve_user_id
    yield
    dpop_module._resolve_user_id = original


# --------------------------------------------------------------------------- #
# Flag off — hard no-op
# --------------------------------------------------------------------------- #
def test_flag_off_is_hard_noop_even_with_install_id(keypair) -> None:
    public_jwk, _ = keypair
    client, touched = _build_app(enable=False, devices={INSTALL_ID: _make_device(public_jwk)})
    # Garbage install_id + no proof would 401 if enforced; with the flag off
    # the middleware must not inspect headers at all.
    resp = client.get(
        "/api/patients/p1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: "anything"},
    )
    assert resp.status_code == 200
    assert touched == []


# --------------------------------------------------------------------------- #
# Flag on
# --------------------------------------------------------------------------- #
def test_no_install_id_passes_as_legacy(keypair) -> None:
    public_jwk, _ = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    resp = client.get("/api/patients/p1", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200


@pytest.mark.parametrize("path", ROUTES)
def test_valid_proof_passes_and_touches_last_seen(keypair, path: str) -> None:
    public_jwk, signing_key = keypair
    client, touched = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    htu = f"http://testserver{path}"
    proof = _sign_proof(signing_key, htm="POST", htu=htu)
    resp = client.post(
        path,
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: proof,
        },
    )
    assert resp.status_code == 200
    assert touched == [INSTALL_ID]


def test_query_string_is_stripped_from_htu(keypair) -> None:
    """A proof signed for the path (no query) must validate a request that
    carries a query string — htu comparison strips the query."""
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    proof = _sign_proof(signing_key, htm="GET", htu="http://testserver/api/patients/p1")
    resp = client.get(
        "/api/patients/p1?foo=bar",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    assert resp.status_code == 200


def _assert_invalid_proof(resp) -> None:
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == WWW_AUTHENTICATE_VALUE


def test_install_id_without_proof_is_rejected(keypair) -> None:
    public_jwk, _ = keypair
    client, touched = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID},
    )
    _assert_invalid_proof(resp)
    assert touched == []


def test_bad_signature_is_rejected(keypair) -> None:
    public_jwk, _ = keypair
    # Sign with a DIFFERENT key than the one enrolled.
    _, other_signing_key = _make_keypair()
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    proof = _sign_proof(other_signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_wrong_htu_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    # Proof signed for a different path than the one requested.
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/patients/OTHER")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_wrong_method_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    proof = _sign_proof(signing_key, htm="GET", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_stale_iat_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    stale = int(time.time()) - 120  # outside the ±60s window
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1", iat=stale)
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_future_iat_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    future = int(time.time()) + 120
    proof = _sign_proof(
        signing_key, htm="POST", htu="http://testserver/api/sessions/s1", iat=future
    )
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_replayed_jti_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    jti = str(uuid.uuid4())
    htu = "http://testserver/api/sessions/s1"
    first = _sign_proof(signing_key, htm="POST", htu=htu, jti=jti)
    headers = {"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: first}
    assert client.post("/api/sessions/s1", headers=headers).status_code == 200

    # A second proof reusing the same jti (even freshly signed) must fail.
    replay = _sign_proof(signing_key, htm="POST", htu=htu, jti=jti)
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: replay},
    )
    _assert_invalid_proof(resp)


def test_unknown_install_id_is_rejected(keypair) -> None:
    _public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={})  # empty registry
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_revoked_install_id_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    device = _make_device(public_jwk, revoked=True)
    client, _ = _build_app(enable=True, devices={INSTALL_ID: device})
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_cross_user_install_id_is_rejected(keypair) -> None:
    """The device is enrolled to a different user than the token resolves to."""
    public_jwk, signing_key = keypair
    device = _make_device(public_jwk, user_id="some-other-user")
    client, _ = _build_app(enable=True, devices={INSTALL_ID: device})
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)


def test_install_id_without_bearer_is_rejected(keypair) -> None:
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},  # no Authorization
    )
    _assert_invalid_proof(resp)


def test_spoofed_forwarded_host_falls_back_to_request_host(keypair) -> None:
    """An untrusted X-Forwarded-Host must NOT shift the htu comparison.

    The header is client-controlled; if the middleware honored it an
    attacker could pick the host half of the htu and replay a proof signed
    for an arbitrary host. With ``evil.example`` untrusted, the middleware
    ignores it and canonicalizes against the real request host
    (``testserver``); since ``testserver`` is a configured (trusted) host,
    the ``https`` from X-Forwarded-Proto is honored for the scheme:

    - a proof signed for the spoofed host → 401 (host not shifted),
    - a proof signed for ``https://testserver`` (real host) → 200.
    """
    public_jwk, signing_key = keypair
    client, _ = _build_app(enable=True, devices={INSTALL_ID: _make_device(public_jwk)})

    spoof_proof = _sign_proof(signing_key, htm="POST", htu="https://evil.example/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: spoof_proof,
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        },
    )
    _assert_invalid_proof(resp)

    real_proof = _sign_proof(signing_key, htm="POST", htu="https://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: real_proof,
            "X-Forwarded-Host": "evil.example",
            "X-Forwarded-Proto": "https",
        },
    )
    assert resp.status_code == 200


def test_trusted_forwarded_host_is_honored(keypair) -> None:
    """When the forwarded host IS trusted (configured public host), the
    htu canonicalizes against the external URL the client signed."""
    public_jwk, signing_key = keypair
    client, _ = _build_app(
        enable=True,
        devices={INSTALL_ID: _make_device(public_jwk)},
        app_url="https://app.pablo.health",
    )
    proof = _sign_proof(signing_key, htm="POST", htu="https://app.pablo.health/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: proof,
            "X-Forwarded-Host": "app.pablo.health",
            "X-Forwarded-Proto": "https",
        },
    )
    assert resp.status_code == 200


def test_preserved_host_without_forwarded_host_is_honored(keypair) -> None:
    """Behind Google Cloud's external load balancer the original Host header is
    preserved and NO X-Forwarded-Host is sent. The middleware must canonicalize
    against that preserved public host, with X-Forwarded-Proto (which Cloud Run
    does set) supplying the https scheme — otherwise every enrolled-companion
    request 401s on a scheme mismatch (http reconstructed vs https signed)."""
    public_jwk, signing_key = keypair
    client, _ = _build_app(
        enable=True,
        devices={INSTALL_ID: _make_device(public_jwk)},
        dpop_trusted_hosts="app.pablo.health",
    )
    proof = _sign_proof(signing_key, htm="POST", htu="https://app.pablo.health/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: proof,
            "Host": "app.pablo.health",
            "X-Forwarded-Proto": "https",
            # No X-Forwarded-Host — the GCP external LB preserves Host instead.
        },
    )
    assert resp.status_code == 200


def test_api_origin_forwarded_host_is_honored(keypair) -> None:
    """The API's own public origin (backend_base_url) is trusted even when
    app_url names a different host.

    The companion signs proofs against the API host it talks to, which is
    backend_base_url — not necessarily the Stripe frontend return URL in
    app_url. With app_url left at the localhost default, forwarding the
    backend_base_url host must still canonicalize against the external URL
    the client signed."""
    public_jwk, signing_key = keypair
    client, _ = _build_app(
        enable=True,
        devices={INSTALL_ID: _make_device(public_jwk)},
        backend_base_url="https://api.pablo.health",
        app_url="http://localhost:3000",
    )
    proof = _sign_proof(signing_key, htm="POST", htu="https://api.pablo.health/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={
            "Authorization": "Bearer t",
            INSTALL_ID_HEADER: INSTALL_ID,
            DPOP_HEADER: proof,
            "X-Forwarded-Host": "api.pablo.health",
            "X-Forwarded-Proto": "https",
        },
    )
    assert resp.status_code == 200


def test_unresolvable_token_is_rejected(keypair) -> None:
    """install_id present but the bearer token resolves to no user → 401."""
    public_jwk, signing_key = keypair
    client, _ = _build_app(
        enable=True, devices={INSTALL_ID: _make_device(public_jwk)}, resolve_user_id=None
    )
    proof = _sign_proof(signing_key, htm="POST", htu="http://testserver/api/sessions/s1")
    resp = client.post(
        "/api/sessions/s1",
        headers={"Authorization": "Bearer t", INSTALL_ID_HEADER: INSTALL_ID, DPOP_HEADER: proof},
    )
    _assert_invalid_proof(resp)
