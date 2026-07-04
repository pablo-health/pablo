# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Server-enforced idle session timeout (HIPAA §164.312(a)(2)(iii)).

Firebase refresh tokens last ~30 days, so a Safari tab restored from
bfcache or a fresh app launch silently re-authenticates the user
against PHI long after they've stopped touching the device. The
frontend `IdleTimeout` component handles the warning dialog and
proactive logout for active tabs, but it can be bypassed (suspended
JS, tampered client, replayed token). This module is the backend
safety net that rejects any request whose session has gone idle for
longer than ``settings.idle_timeout_seconds``.

Design: two Redis keys per (uid, auth_time):

  idle:session:{uid}:{auth_time}    long-TTL marker — "we've seen this sign-in"
  idle:activity:{uid}:{auth_time}   short-TTL heartbeat — refreshed on each request

The pair lets us disambiguate the three "missing key" cases that a
single-key design conflates:

  marker present, activity present  → active session; refresh activity.
  marker present, activity missing  → activity TTL expired → idle timeout.
                                       Delete marker, set revoked tombstone;
                                       reject 401 IDLE_TIMEOUT.
  marker missing                    → first request after sign-in (or Redis
                                       flush). Create both; allow. A flush
                                       silently resets every active session's
                                       idle window — accepted tradeoff vs.
                                       false-positive lockouts.

A third key, idle:revoked:{uid}:{auth_time}, tombstones a timed-out
session. Without it, "marker missing" can't distinguish a fresh sign-in
from a session burned minutes ago — a refresh-token swap reuses the same
auth_time, so the next request would revive the session via the
"create both, allow" branch. The tombstone makes idle-out terminal for
that auth_time; only a real re-auth (new auth_time) recovers.

Skipped when ``settings.use_redis`` is false (single-instance OSS
self-hosters who run without Redis fall back to the client-side
IdleTimeout component as their only protection).

The pure function ``check_and_touch`` is wired in via
``auth.service.enforce_idle_session`` (FastAPI Depends wrapper) — kept
separate to avoid a circular import between service.py and this module.
"""

import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from ..redis_client import get_redis_client
from ..settings import get_settings

logger = logging.getLogger(__name__)

# Marker TTL matches Firebase's refresh-token lifetime so stale markers
# for abandoned sessions self-expire instead of accumulating forever.
_SESSION_MARKER_TTL_SECONDS = 31 * 24 * 60 * 60


def _session_marker_key(uid: str, auth_time: int) -> str:
    return f"idle:session:{uid}:{auth_time}"


def _activity_key(uid: str, auth_time: int) -> str:
    return f"idle:activity:{uid}:{auth_time}"


def _revoked_key(uid: str, auth_time: int) -> str:
    return f"idle:revoked:{uid}:{auth_time}"


def _idle_timeout_exc() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "IDLE_TIMEOUT",
                "message": "Session expired due to inactivity. Please sign in again.",
                "details": {},
            }
        },
    )


@dataclass(frozen=True)
class SessionPeek:
    """Result of a read-only liveness check for one session.

    ``enforced`` is False when the idle control is off (dev mode or no
    Redis) — the client falls back to its local activity clock.
    ``seconds_remaining`` is None when unknown (enforcement off).
    """

    enforced: bool
    active: bool
    seconds_remaining: int | None


def _session_identifiers(decoded_token: dict[str, Any]) -> tuple[str, int]:
    """Extract (subject, auth_time) or raise 401 INVALID_TOKEN.

    Subject is the provider's stable user id: Firebase puts it in `uid`,
    OIDC issuers (e.g. Keycloak) in `sub`. `auth_time` anchors the marker
    to one authentication event and is stable across token refreshes, so
    the idle clock survives refresh — we keep requiring it as the freshness
    anchor. Firebase always carries it; OIDC interactive (auth-code) flows
    do too. A token missing either fails closed.
    """
    subject = decoded_token.get("uid") or decoded_token.get("sub")
    auth_time = decoded_token.get("auth_time")
    if not subject or not isinstance(auth_time, int):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Token missing session identifiers",
                    "details": {},
                }
            },
        )
    return str(subject), auth_time


def check_and_touch(decoded_token: dict[str, Any]) -> None:
    """Enforce the idle window for this token's session.

    Raises HTTPException(401 IDLE_TIMEOUT) when the activity heartbeat
    has expired. Returns silently when the session is fresh, active, or
    when the check is disabled (dev mode, Redis unavailable).
    """
    settings = get_settings()
    if settings.is_development:
        return

    redis = get_redis_client()
    if redis is None:
        return

    subject, auth_time = _session_identifiers(decoded_token)

    marker_key = _session_marker_key(str(subject), auth_time)
    activity_key = _activity_key(str(subject), auth_time)
    revoked_key = _revoked_key(str(subject), auth_time)
    idle_ttl = settings.idle_timeout_seconds

    try:
        if redis.exists(revoked_key):
            # Already timed out; a refresh-token swap must not re-arm it.
            logger.info(
                "Rejected revoked idle session: subject=%s auth_time=%s", subject, auth_time
            )
            raise _idle_timeout_exc()

        marker_exists = bool(redis.exists(marker_key))
        if not marker_exists:
            pipe = redis.pipeline()
            pipe.set(marker_key, "1", ex=_SESSION_MARKER_TTL_SECONDS)
            pipe.set(activity_key, "1", ex=idle_ttl)
            pipe.execute()
            return

        if redis.exists(activity_key):
            redis.set(activity_key, "1", ex=idle_ttl)
            return

        # Marker present, activity absent → idle TTL elapsed. Burn the
        # marker and tombstone this auth_time atomically.
        pipe = redis.pipeline()
        pipe.delete(marker_key)
        pipe.set(revoked_key, "1", ex=_SESSION_MARKER_TTL_SECONDS)
        pipe.execute()
        logger.info("Idle session timeout: subject=%s auth_time=%s", subject, auth_time)
        raise _idle_timeout_exc()
    except HTTPException:
        raise
    except Exception as exc:
        # A Redis error must not silently disable the HIPAA auto-logoff
        # control. Default (fail closed): reject with 503 so the caller
        # retries once Redis recovers — without falsely burning the session
        # as an idle timeout. Self-hosters who prefer availability over
        # strict enforcement can opt into the old allow-through behaviour
        # via IDLE_SESSION_FAIL_OPEN=true.
        if settings.idle_session_fail_open:
            logger.error(
                "Idle session check failed; allowing request (IDLE_SESSION_FAIL_OPEN=true): %s",
                exc,
            )
            return
        logger.error("Idle session check failed; failing closed (503): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "IDLE_CHECK_UNAVAILABLE",
                    "message": "Session service temporarily unavailable. Please retry.",
                    "details": {},
                }
            },
        ) from exc


def _peek_remaining_seconds(redis: Any, subject: str, auth_time: int, idle_ttl: int) -> int:
    """Seconds left in the idle window for this session; 0 when dead."""
    if redis.exists(_revoked_key(subject, auth_time)):
        return 0

    ttl = redis.ttl(_activity_key(subject, auth_time))
    if ttl > 0:
        return int(ttl)

    if redis.exists(_session_marker_key(subject, auth_time)):
        # Marker present, activity expired → idle window has elapsed.
        return 0

    # No keys at all: a session that hasn't made its first enforced
    # request yet (or Redis was flushed). check_and_touch will seed
    # the keys and allow it, so report the full window.
    return idle_ttl


def peek(decoded_token: dict[str, Any]) -> SessionPeek:
    """Read-only liveness check: report the session's idle state WITHOUT
    refreshing the activity heartbeat.

    This is what lets the frontend ask "is this session still alive?"
    (on dashboard entry, tab restore, and on a poll) without the act of
    asking keeping every open tab's session alive forever. Pure read:
    no keys are created, refreshed, or tombstoned — an idle-elapsed
    session reported here as inactive is burned by ``check_and_touch``
    on its next real request (or expires with its marker TTL).

    ``seconds_remaining`` is the activity key's TTL — the time left
    before the session idles out absent further requests.
    """
    settings = get_settings()
    redis = None if settings.is_development else get_redis_client()
    if redis is None:
        return SessionPeek(enforced=False, active=True, seconds_remaining=None)

    subject, auth_time = _session_identifiers(decoded_token)

    try:
        remaining = _peek_remaining_seconds(
            redis, subject, auth_time, settings.idle_timeout_seconds
        )
        return SessionPeek(enforced=True, active=remaining > 0, seconds_remaining=remaining)
    except Exception as exc:
        # Mirror check_and_touch's error posture, without inventing a new
        # failure mode for a read-only endpoint: fail open reports
        # unenforced (client falls back to its local clock), fail closed
        # surfaces the same 503 as the enforcing path.
        if settings.idle_session_fail_open:
            logger.error("Idle session peek failed; reporting unenforced: %s", exc)
            return SessionPeek(enforced=False, active=True, seconds_remaining=None)
        logger.error("Idle session peek failed; failing closed (503): %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "IDLE_CHECK_UNAVAILABLE",
                    "message": "Session service temporarily unavailable. Please retry.",
                    "details": {},
                }
            },
        ) from exc
