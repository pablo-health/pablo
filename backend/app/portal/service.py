# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The credential lifecycle: issue, redeem, refresh, revoke.

Two factors, two channels. The clinician's invitation emails a magic link
(possession of the invite token) and Pablo texts a one-time code (possession
of the phone). Redemption requires BOTH, so a leaked link alone never mints
a session — which is what § 164.312(d) is asking for. Single-use and
attempt-limiting come from the challenge keyed on the token's ``jti``.

The clock is injected so callers control time and TTL assertions stay exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from . import tokens
from .errors import (
    ExpiredInviteError,
    InvalidInviteError,
    InvalidStepUpError,
    InviteAlreadyRedeemedError,
    SessionLifetimeExceededError,
    SessionRevokedError,
    TooManyAttemptsError,
)
from .otp import generate_otp, hash_otp, verify_otp
from .store import (
    InviteChallenge,
    PortalAuthStore,
    PortalSessionRecord,
    PortalSessionStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .delivery import SmsGateway

#: What the patient reads. The code and how long they have, and nothing
#: else: a text message is not the place for a name, a practice or a reason.
OTP_MESSAGE = "Your Pablo verification code is {otp}. It expires in 15 minutes."


@dataclass(frozen=True)
class PortalAuthConfig:
    signing_key: str
    invite_ttl_seconds: int = 900  # 15 min
    session_ttl_seconds: int = 3600  # 1 h
    otp_length: int = 6
    max_otp_attempts: int = 5
    #: Ceiling on sliding renewal, measured from the ORIGINAL redemption
    #: rather than from the current token. Refresh rotates the jti, so
    #: without this a session renews forever an hour at a time and the
    #: two-factor proof recedes indefinitely into the past. 30 days.
    session_max_lifetime_seconds: int = 2_592_000


@dataclass(frozen=True)
class InviteIssued:
    """``token`` goes into the magic link; the code was delivered
    out-of-band. ``jti`` is the challenge handle — it carries no secret, so
    it is what a log or an audit row may name."""

    token: str
    jti: str
    #: When the invitation stops redeeming (unix seconds). Returned to the
    #: clinician so the screen can say how long the patient has, without the
    #: clinician ever seeing the token itself.
    expires_at: int


@dataclass(frozen=True)
class PatientSession:
    session_token: str
    patient_id: str
    tenant: str
    #: Names the session row this token is only valid while it lives — the
    #: handle a revoke or a rotation acts on.
    jti: str
    expires_at: int


class PortalAuthService:
    """Issue, redeem, refresh and revoke — the whole credential lifecycle.

    ``sessions`` is optional so the pure unit tests (and any caller that
    only mints tokens) can run without a revocation list. It is NOT optional
    in production: with no store, a minted token is valid until it expires
    and nothing can call it back, so every path that hands a real patient a
    session passes one, and :meth:`refresh` / :meth:`revoke_patient` refuse
    outright without it rather than quietly succeeding at nothing.
    """

    def __init__(
        self,
        *,
        config: PortalAuthConfig,
        store: PortalAuthStore,
        sms: SmsGateway,
        now: Callable[[], int],
        sessions: PortalSessionStore | None = None,
    ) -> None:
        if not config.signing_key:
            # Fail closed: an empty key would sign forgeable tokens.
            raise ValueError("portal signing key is not configured")
        self._config = config
        self._store = store
        self._sms = sms
        self._now = now
        self._sessions = sessions

    def _require_sessions(self) -> PortalSessionStore:
        if self._sessions is None:
            raise ValueError("portal session store is not configured")
        return self._sessions

    def _mint_session(
        self, *, patient_id: str, tenant: str, now: int, chain_started_at: int
    ) -> PatientSession:
        """Mint a session token AND record the row that keeps it valid.

        Recording first means the credential never exists before the thing
        that can revoke it. A token whose row was never written is refused
        by the resolver, which is the safe direction; a row with no token is
        inert.
        """
        cfg = self._config
        jti = uuid4().hex
        expires_at = now + cfg.session_ttl_seconds
        if self._sessions is not None:
            self._sessions.record(
                PortalSessionRecord(
                    jti=jti,
                    patient_id=patient_id,
                    issued_at=now,
                    expires_at=expires_at,
                    chain_started_at=chain_started_at,
                )
            )
        session_token = tokens.mint_session_token(
            signing_key=cfg.signing_key,
            claims=tokens.SessionClaims(jti=jti, patient_id=patient_id, tenant=tenant),
            lifetime=tokens.TokenLifetime(issued_at=now, ttl_seconds=cfg.session_ttl_seconds),
        )
        return PatientSession(
            session_token=session_token,
            patient_id=patient_id,
            tenant=tenant,
            jti=jti,
            expires_at=expires_at,
        )

    def issue_invite(
        self,
        *,
        patient_id: str,
        tenant: str,
        phone: str,
        purpose: str = "intake",
    ) -> InviteIssued:
        """Mint a single-use invite token and text the step-up code. The
        caller emails the returned token as a magic link.

        The text is sent before the challenge is persisted: if the send
        raises, no challenge was ever stored for this jti, so nothing
        orphaned is left behind and the token in hand can never be redeemed.
        """
        cfg = self._config
        issued_at = self._now()
        expires_at = issued_at + cfg.invite_ttl_seconds
        jti = uuid4().hex
        otp = generate_otp(cfg.otp_length)

        token = tokens.mint_invite_token(
            signing_key=cfg.signing_key,
            claims=tokens.InviteClaims(
                jti=jti, patient_id=patient_id, tenant=tenant, purpose=purpose
            ),
            lifetime=tokens.TokenLifetime(issued_at=issued_at, ttl_seconds=cfg.invite_ttl_seconds),
        )
        self._sms.send(to=phone, body=OTP_MESSAGE.format(otp=otp))
        self._store.put_challenge(
            InviteChallenge(
                jti=jti,
                patient_id=patient_id,
                tenant=tenant,
                otp_hash=hash_otp(otp, pepper=cfg.signing_key),
                expires_at=expires_at,
            )
        )
        return InviteIssued(token=token, jti=jti, expires_at=expires_at)

    def redeem(self, *, token: str, otp: str) -> PatientSession:
        """Verify link possession AND the texted code, then mint a
        patient-session token. Single-use and attempt-limited."""
        cfg = self._config
        claims = tokens.verify_invite_token(signing_key=cfg.signing_key, token=token)

        challenge = self._store.get_challenge(claims.jti)
        if challenge is None:
            raise InvalidInviteError("no challenge for this invitation")
        if challenge.consumed:
            raise InviteAlreadyRedeemedError(claims.jti)
        now = self._now()
        if now >= challenge.expires_at:
            raise ExpiredInviteError(claims.jti)
        if challenge.attempts >= cfg.max_otp_attempts:
            raise TooManyAttemptsError(claims.jti)

        if not verify_otp(otp, otp_hash=challenge.otp_hash, pepper=cfg.signing_key):
            self._store.increment_attempts(claims.jti)
            raise InvalidStepUpError(claims.jti)

        # Success: burn the invitation before issuing the session so a race
        # cannot redeem twice.
        self._store.mark_consumed(claims.jti)
        # This redemption starts the chain: every later refresh carries
        # ``now`` forward as ``chain_started_at``, and the ceiling is
        # measured from here.
        return self._mint_session(
            patient_id=claims.patient_id,
            tenant=claims.tenant,
            now=now,
            chain_started_at=now,
        )

    def refresh(self, *, token: str) -> PatientSession:
        """Rotate a live session into a fresh one.

        Rotation rather than extension: the new token gets a new ``jti`` and
        a new row, and the presented one is retired in the same breath. So a
        stolen token stops working the moment the real patient renews, and a
        revoked session can never be refreshed back into existence.

        Bounded by ``session_max_lifetime_seconds`` from the chain's
        original redemption — past that the patient proves both factors
        again rather than riding one redemption indefinitely.
        """
        cfg = self._config
        sessions = self._require_sessions()
        claims = tokens.verify_session_token(signing_key=cfg.signing_key, token=token)

        now = self._now()
        current = sessions.get(claims.jti)
        if current is None or not current.is_live(now=now):
            # Unknown, revoked, rotated away, or expired — all one refusal.
            raise SessionRevokedError(claims.jti)
        if current.patient_id != claims.patient_id:
            # The signature is good and the row exists, but they disagree
            # about who this is. Nothing legitimate produces that, and the
            # token's claim is the half an attacker would control.
            raise SessionRevokedError(claims.jti)
        if now - current.chain_started_at >= cfg.session_max_lifetime_seconds:
            raise SessionLifetimeExceededError(claims.jti)

        # Retire the presented session first: if anything below fails, the
        # patient has lost a credential (recoverable — they redeem again)
        # rather than kept two live ones.
        sessions.revoke(current.jti, at=now)
        return self._mint_session(
            patient_id=current.patient_id,
            tenant=claims.tenant,
            now=now,
            chain_started_at=current.chain_started_at,
        )

    def revoke_patient(self, *, patient_id: str) -> int:
        """Revoke every live session for one patient; return how many.

        The clinician kill switch. Outstanding invite challenges are burned
        by the route alongside this — a revoke that left a redeemable
        invitation in flight would undo itself a minute later.
        """
        return self._require_sessions().revoke_all_for_patient(patient_id, at=self._now())
