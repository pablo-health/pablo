# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The credential lifecycle: issue, request a code, redeem, refresh, revoke.

Two factors, two channels. The clinician's invitation emails a magic link
(possession of the invite token) and Pablo texts a one-time code (possession
of the phone). Redemption requires BOTH, so a leaked link alone never mints
a session — which is what § 164.312(d) is asking for. Single-use and
attempt-limiting come from the challenge keyed on the token's ``jti``.

The code is texted when the patient asks for it from the page the link
opens, not when the invitation is sent. So the link can live for days while
the code still lives for minutes: the window a code is good for starts when
the patient is there to use it, rather than when the invitation went out.

The clock is injected so callers control time and TTL assertions stay exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from . import tokens
from .errors import (
    ExpiredCodeError,
    ExpiredInviteError,
    InvalidInviteError,
    InvalidStepUpError,
    InviteAlreadyRedeemedError,
    SessionLifetimeExceededError,
    SessionRevokedError,
    StepUpChannelMissingError,
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
    #: How long the emailed link keeps working. Days rather than minutes:
    #: the link alone never mints a session, and most patients do not open
    #: an invitation the moment it arrives.
    invite_ttl_seconds: int = 604_800  # 7 days
    #: How long a texted code keeps working, counted from the request that
    #: texted it. :data:`OTP_MESSAGE` says fifteen minutes; keep them equal.
    otp_ttl_seconds: int = 900  # 15 min
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
    """``token`` goes into the magic link; the code is texted later, when
    the patient asks for it. ``jti`` is the challenge handle — it carries no
    secret, so it is what a log or an audit row may name."""

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

    Both stores are optional, and for the same reason from opposite ends:
    the lifecycle splits cleanly into the invitation half and the session
    half, and a caller often has business with only one of them. Signing a
    patient out touches no invitation; the pure unit tests that only mint
    tokens touch no revocation list.

    Optional is not lenient. A method that needs a store it was not given
    raises rather than quietly succeeding at nothing — because "quietly
    succeeding at nothing" here means a credential nobody can call back, or
    a sign-out that signed nobody out. Every production path passes the
    store its work actually needs.
    """

    def __init__(
        self,
        *,
        config: PortalAuthConfig,
        sms: SmsGateway,
        now: Callable[[], int],
        store: PortalAuthStore | None = None,
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

    def _require_store(self) -> PortalAuthStore:
        if self._store is None:
            raise ValueError("portal challenge store is not configured")
        return self._store

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
        purpose: str = "intake",
    ) -> InviteIssued:
        """Mint a single-use invite token and record its challenge. The
        caller emails the returned token as a magic link.

        Nothing is texted here. The challenge starts with no code at all, so
        the invitation cannot be redeemed until the patient opens the link
        and asks for one (:meth:`request_code`).
        """
        cfg = self._config
        issued_at = self._now()
        expires_at = issued_at + cfg.invite_ttl_seconds
        jti = uuid4().hex

        token = tokens.mint_invite_token(
            signing_key=cfg.signing_key,
            claims=tokens.InviteClaims(
                jti=jti, patient_id=patient_id, tenant=tenant, purpose=purpose
            ),
            lifetime=tokens.TokenLifetime(issued_at=issued_at, ttl_seconds=cfg.invite_ttl_seconds),
        )
        self._require_store().put_challenge(
            InviteChallenge(
                jti=jti,
                patient_id=patient_id,
                tenant=tenant,
                otp_hash=None,
                expires_at=expires_at,
            )
        )
        return InviteIssued(token=token, jti=jti, expires_at=expires_at)

    def _open_challenge(self, token: str) -> tuple[tokens.InviteClaims, InviteChallenge, int]:
        """The invitation behind *token*, and the time it was checked at.

        The refusals :meth:`request_code` and :meth:`redeem` share: unknown,
        spent, expired, attempt-capped. A code is never texted for an
        invitation that could not then be redeemed with it.
        """
        cfg = self._config
        claims = tokens.verify_invite_token(signing_key=cfg.signing_key, token=token)

        challenge = self._require_store().get_challenge(claims.jti)
        if challenge is None:
            raise InvalidInviteError("no challenge for this invitation")
        if challenge.consumed:
            raise InviteAlreadyRedeemedError(claims.jti)
        now = self._now()
        if now >= challenge.expires_at:
            raise ExpiredInviteError(claims.jti)
        if challenge.attempts >= cfg.max_otp_attempts:
            raise TooManyAttemptsError(claims.jti)
        return claims, challenge, now

    def request_code(self, *, token: str, phone_for: Callable[[str], str | None]) -> None:
        """Text a fresh code for this invitation, replacing any earlier one.

        ``phone_for`` reads the number off the patient's chart. The caller
        never supplies a number: the second factor goes where the practice
        recorded it, not wherever a holder of the link would like.

        A resend restarts the code window and retires the previous code, but
        leaves the attempt counter alone — the cap is per invitation, so
        asking for a new code does not buy more guesses.

        The text is sent before the new hash is stored: if the send raises,
        the stored state is exactly what it was, and a code the patient
        already holds still works.
        """
        cfg = self._config
        claims, _challenge, now = self._open_challenge(token)
        phone = phone_for(claims.patient_id)
        if not phone:
            raise StepUpChannelMissingError(claims.jti)

        otp = generate_otp(cfg.otp_length)
        self._sms.send(to=phone, body=OTP_MESSAGE.format(otp=otp))
        self._require_store().set_code(
            claims.jti,
            otp_hash=hash_otp(otp, pepper=cfg.signing_key),
            code_expires_at=now + cfg.otp_ttl_seconds,
        )

    def redeem(self, *, token: str, otp: str) -> PatientSession:
        """Verify link possession AND the texted code, then mint a
        patient-session token. Single-use and attempt-limited."""
        cfg = self._config
        claims, challenge, now = self._open_challenge(token)

        if challenge.otp_hash is None:
            # No code has been requested, so there is nothing to match.
            raise ExpiredCodeError(claims.jti)
        # An invitation issued before codes were requested separately has a
        # hash and no code window of its own; its own expiry, checked above,
        # is the window.
        if challenge.code_expires_at is not None and now >= challenge.code_expires_at:
            raise ExpiredCodeError(claims.jti)

        if not verify_otp(otp, otp_hash=challenge.otp_hash, pepper=cfg.signing_key):
            self._require_store().increment_attempts(claims.jti)
            raise InvalidStepUpError(claims.jti)

        # Success: burn the invitation before issuing the session so a race
        # cannot redeem twice.
        self._require_store().mark_consumed(claims.jti)
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

    def revoke_session(self, *, jti: str) -> None:
        """Retire ONE session. Signing out on this device and no other.

        Takes a ``jti`` the caller resolved from a live principal, not a
        token: by the time a patient can ask to sign out, the front door has
        already verified their session and named its row, so re-verifying
        here would be checking the same signature twice.

        Idempotent, and it does not care whether the row is live — signing
        out of a session that expired a second ago is a no-op the patient
        should never hear about.
        """
        self._require_sessions().revoke(jti, at=self._now())

    def has_active_grant(self, *, patient_id: str) -> bool:
        """Has this patient been given portal access and not had it taken away?

        There is no column that records a grant, and adding one would be a
        third piece of state to keep in step with the two that already
        decide everything. So the question is answered from those two: the
        practice's kill switch revokes every session row AND consumes every
        outstanding invitation, in one transaction, so a patient it has been
        used on has neither an unrevoked session nor an unspent invitation.
        Anyone else who was ever invited has at least one of the two.

        What the two arms cover between them:

        * an invitation sent and not yet redeemed — unconsumed challenge;
        * an invitation that expired unredeemed — unconsumed challenge, and
          the most ordinary reason someone asks for a new link;
        * a patient who signed in months ago and whose session lapsed —
          unrevoked row, because lapsing is not withdrawal;
        * a patient who signed themselves out of every device — still
          unrevoked invitations or, if none, no grant, which is the safe
          direction: a clinician re-invite restores them.

        Never a reason to answer a caller differently. The recovery route
        answers identically either way; this only decides whether a link is
        sent.
        """
        if self._sessions is not None and self._sessions.has_unrevoked_session(patient_id):
            return True
        return self._require_store().has_unconsumed_challenge(patient_id)
