# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""DPoP proof-of-possession middleware (stage 2).

Verifies an RFC 9449-style proof, bound to an enrolled ``install_id``
rather than the access token's ``cnf.jkt`` claim. The deviation (and
its security justification) is documented in
``docs/design/companion-dpop-binding.md``; in short, Firebase mints our
id_tokens and we don't control their claims, so the proof key is bound
to the device row the companion enrolled instead.

What this middleware does, when ``ENABLE_DPOP_VALIDATION`` is on and the
request carries an ``X-Install-ID`` header:

1. Resolve the authenticated user from the bearer token.
2. Look up ``companion_devices(user_id, install_id)``. Missing, revoked,
   or owned by a different user → 401.
3. Parse the ``DPoP`` compact JWS and verify:
   - ``htm`` == request method,
   - ``htu`` == request scheme+host+path (query stripped),
   - ``iat`` within ±60s of server time,
   - ``jti`` unseen (replay guard, 5-minute TTL — Redis-shared across
     instances when configured, per-process LRU otherwise; see
     ``services/replay_guard.py``),
   - signature verifies against the device's stored public JWK.
4. On success, touch ``last_seen`` and let the request proceed.

Failure at any proof check returns ``401`` with
``WWW-Authenticate: DPoP error="invalid_proof"``.

Posture matrix (mirrors the design doc's stage-2 table):

============================  ===============  ===========  ==================
``ENABLE_DPOP_VALIDATION``    ``X-Install-ID`` ``DPoP``     Outcome
============================  ===============  ===========  ==================
false                         —                —            hard no-op pass
true                          absent           —            pass (legacy)
true                          present          invalid      401 invalid_proof
true                          present          valid        pass + touch
============================  ===============  ===========  ==================

This is the permissive stage-2 layer: presence of ``X-Install-ID``
triggers enforcement; absence is allowed so existing clients keep
working. Tightening "every companion request MUST carry a proof" is a
later flip and out of scope here.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import jwt
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from ..services.replay_guard import ReplayGuard, get_replay_guard

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.types import ASGIApp

    from ..models.companion_device import CompanionDevice
    from ..settings import Settings

logger = logging.getLogger(__name__)

INSTALL_ID_HEADER = "X-Install-ID"
DPOP_HEADER = "DPoP"
# RFC 6750-style challenge value reused for the DPoP scheme. Constant —
# we never echo proof material back to the client.
WWW_AUTHENTICATE_VALUE = 'DPoP error="invalid_proof"'

# ES256 (P-256 / SHA-256) is the only proof algorithm the companions
# emit (Secure Enclave / TPM P-256 keys). Pinning the algorithm here is
# an alg-confusion guard: it rejects ``none`` and any token that tries to
# downgrade to a symmetric algorithm verifiable with the public key bytes.
_DPOP_ALGORITHMS = ["ES256"]

# ``iat`` freshness window. A proof more than this many seconds away from
# server time (either direction — covers clock skew + a captured proof
# replayed after the fact) is rejected.
_IAT_WINDOW_SECONDS = 60

# Replay cache TTL. Comfortably exceeds the ±60s iat window so a proof
# can never fall out of the cache while it is still inside its freshness
# window. See docs/design/companion-dpop-binding.md § Nonces.
_JTI_TTL_SECONDS = 300
_JTI_NAMESPACE = "dpop:jti"


class DPoPValidationError(Exception):
    """Raised internally when a proof fails any RFC 9449 check.

    Carries no client-facing detail — the middleware collapses every
    failure into the same generic ``invalid_proof`` challenge so an
    attacker can't distinguish "wrong htu" from "stale iat" from
    "replayed jti" (anti-oracle).
    """


def _trusted_hosts(settings: Settings) -> frozenset[str]:
    """The set of public hosts the middleware may honor from a forwarded header.

    Derived from ``settings.app_url`` (its netloc is always trusted) plus
    any comma-separated ``DPOP_TRUSTED_HOSTS`` override for deployments
    that serve the API under more than one public hostname. Hosts are
    compared case-insensitively (DNS is case-insensitive). An entry that
    doesn't parse to a netloc is dropped rather than failing closed on the
    whole set.
    """
    hosts: set[str] = set()
    app_host = urlsplit(settings.app_url).netloc
    if app_host:
        hosts.add(app_host.lower())
    for raw in settings.dpop_trusted_hosts.split(","):
        entry = raw.strip()
        if not entry:
            continue
        # Accept either a bare host ("app.example") or a full origin
        # ("https://app.example") — normalize both to a netloc.
        netloc = urlsplit(entry).netloc or entry
        hosts.add(netloc.lower())
    return frozenset(hosts)


def _canonical_htu(request: Request, trusted_hosts: frozenset[str]) -> str:
    """Request scheme+host+path with the query string and fragment stripped.

    RFC 9449 §4.3 compares ``htu`` against the request URI without query
    or fragment. Behind the Cloud Run / LB TLS terminator the raw ASGI
    scheme is ``http`` and the host is an internal one, so we want to
    canonicalize against the externally-visible URL the client actually
    signed — which arrives via ``X-Forwarded-Proto`` / ``X-Forwarded-Host``.

    But both forwarded headers are client-influenceable: a request can
    arrive carrying any ``X-Forwarded-Host`` it likes. If we honored an
    arbitrary forwarded host, an attacker could choose the host half of
    the ``htu`` comparison and replay a proof signed for one deployment
    against another. So we only honor a forwarded host when it is in the
    trusted set (derived from ``settings.app_url`` + the
    ``DPOP_TRUSTED_HOSTS`` override). For an untrusted or absent forwarded
    host we fall back to the raw request host, which the client cannot
    forge (it is the connection's actual ``Host``/authority). The scheme
    is only upgraded from the forwarded header once we've decided to trust
    the host, so a spoofed ``X-Forwarded-Proto`` alone can't shift the
    comparison either.
    """
    forwarded_host = request.headers.get("x-forwarded-host")
    candidate = forwarded_host.split(",")[0].strip() if forwarded_host else ""
    if candidate and candidate.lower() in trusted_hosts:
        host = candidate
        forwarded_proto = request.headers.get("x-forwarded-proto")
        scheme = forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme
    else:
        host = request.url.netloc
        scheme = request.url.scheme
    return urlunsplit((scheme, host, request.url.path, "", ""))


def _normalize_htu(value: str) -> str:
    """Drop query + fragment from a claimed ``htu`` for comparison."""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def verify_dpop_proof(
    proof: str,
    device: CompanionDevice,
    *,
    method: str,
    htu: str,
    replay_cache: ReplayGuard,
    now: float | None = None,
) -> None:
    """Verify a DPoP proof against an enrolled device's key.

    Raises :class:`DPoPValidationError` on any failure. Pure function of
    its arguments (the request-derived ``method``/``htu`` and a replay
    cache), so it is exercised directly by the middleware tests.
    """
    ts = time.time() if now is None else now
    try:
        verify_key = jwt.PyJWK.from_dict(device.device_public_key_jwk).key
    except Exception as err:  # any JWK parse failure is an invalid proof, not a 500
        raise DPoPValidationError("device key is unusable") from err

    try:
        claims = jwt.decode(
            proof,
            verify_key,
            algorithms=_DPOP_ALGORITHMS,
            options={
                "verify_aud": False,
                # iat is validated manually (PyJWT would only reject a
                # future iat); we need a symmetric ±window check.
                "verify_iat": False,
                "require": ["htm", "htu", "iat", "jti"],
            },
        )
    except jwt.PyJWTError as err:
        # No proof material in logs — failure reason only.
        raise DPoPValidationError(f"proof signature/claims invalid: {err}") from err

    if claims.get("htm") != method:
        raise DPoPValidationError("htm mismatch")

    claimed_htu = claims.get("htu")
    if not isinstance(claimed_htu, str) or _normalize_htu(claimed_htu) != htu:
        raise DPoPValidationError("htu mismatch")

    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or abs(ts - float(iat)) > _IAT_WINDOW_SECONDS:
        raise DPoPValidationError("iat outside freshness window")

    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        raise DPoPValidationError("missing jti")
    if not replay_cache.check_and_add(jti, now=ts):
        raise DPoPValidationError("jti replay")


def _resolve_user_id(request: Request, token: str) -> str | None:
    """Resolve the authenticated request to a Pablo user_id, or None.

    Uses the same verifier + identity-mapping path the auth dependencies
    use. The DatabaseSessionMiddleware has already verified+cached the
    identity on ``request.state`` for the common case, so this is a cache
    hit, not a second verification round-trip. Returns ``None`` if the
    token doesn't verify (or no token / identity is present).

    When an ``X-Install-ID`` is present, a ``None`` here is fail-closed:
    the middleware rejects with ``401`` + ``WWW-Authenticate: DPoP
    error="invalid_proof"`` rather than letting the request through. A
    proof can't be bound to a device without a resolved user, so an
    unverifiable token on a companion request is treated as an invalid
    proof. (A companion with a valid install_id but an expired Firebase
    token therefore gets the ``invalid_proof`` challenge rather than a
    standard auth 401 — the companion handles a 401 as re-auth either
    way; see the design doc's redeem-outcome handling.)
    """
    from ..auth.service import _verify_request_identity
    from ..repositories import get_identity_repository

    try:
        identity = _verify_request_identity(request, token)
    except Exception:
        return None

    repo = get_identity_repository()
    return repo.get_user_id(identity.provider, identity.subject_id)


def _default_device_lookup(install_id: str) -> CompanionDevice | None:
    """Load an enrolled device by install_id from the request-scoped session.

    Returns ``None`` when no Postgres session is in scope (no companion
    can be enrolled without one) so the middleware degrades to a
    pass-through rather than 500ing.
    """
    try:
        from ..db import get_db_session
        from ..repositories.postgres.companion_device import (
            PostgresCompanionDeviceRepository,
        )

        return PostgresCompanionDeviceRepository(get_db_session()).get(install_id)
    except RuntimeError:
        return None


def _touch_last_seen(install_id: str) -> None:
    """Best-effort ``last_seen`` bump after a successful proof."""
    try:
        from ..db import get_db_session
        from ..repositories.postgres.companion_device import (
            PostgresCompanionDeviceRepository,
        )

        PostgresCompanionDeviceRepository(get_db_session()).touch_last_seen(install_id)
    except RuntimeError:
        # No session in scope (tests / dev). last_seen is non-critical.
        return


class DPoPMiddleware(BaseHTTPMiddleware):
    """Enforce DPoP proofs on requests that present an ``X-Install-ID``.

    Registered in ``main.py`` *inside* the DB-session middleware so the
    request-scoped session (used for the device lookup) and the cached
    verified identity are both available when ``dispatch`` runs.

    ``device_lookup`` / ``touch`` / ``replay_guard`` are injectable so
    the integration tests can drive the full middleware stack against an
    in-memory device registry without a live Postgres or Redis.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        device_lookup: Callable[[str], CompanionDevice | None] | None = None,
        touch: Callable[[str], None] | None = None,
        replay_guard: ReplayGuard | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._trusted_hosts = _trusted_hosts(settings)
        self._device_lookup = device_lookup or _default_device_lookup
        self._touch = touch or _touch_last_seen
        self._replay_cache = replay_guard or get_replay_guard(
            _JTI_NAMESPACE, ttl_seconds=_JTI_TTL_SECONDS
        )

    def _reject(self) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": {
                    "code": "INVALID_DPOP_PROOF",
                    "message": "DPoP proof validation failed.",
                    "details": {},
                }
            },
            headers={"WWW-Authenticate": WWW_AUTHENTICATE_VALUE},
        )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Hard no-op when the flag is off: no header inspection at all.
        if not self._settings.enable_dpop_validation:
            return await call_next(request)

        install_id = request.headers.get(INSTALL_ID_HEADER)
        # Legacy pass: no install_id → ordinary Firebase-bearer request.
        if not install_id:
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            # An install_id with no bearer token can't be bound to a user.
            return self._reject()
        token = auth_header[len("Bearer ") :]

        user_id = _resolve_user_id(request, token)
        if user_id is None:
            return self._reject()

        device = self._device_lookup(install_id)
        if device is None or device.revoked_at is not None or device.user_id != user_id:
            # Unknown / revoked / cross-user install_id. Same 401 for all
            # three so the response doesn't reveal which condition tripped.
            return self._reject()

        proof = request.headers.get(DPOP_HEADER)
        if not proof:
            return self._reject()

        try:
            verify_dpop_proof(
                proof,
                device,
                method=request.method,
                htu=_canonical_htu(request, self._trusted_hosts),
                replay_cache=self._replay_cache,
            )
        except DPoPValidationError as err:
            # Failure reason logged for ops triage; never the proof itself.
            logger.warning("DPoP proof rejected for install_id=%s: %s", install_id, err)
            return self._reject()

        self._touch(install_id)
        return await call_next(request)
