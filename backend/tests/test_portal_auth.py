# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the portal sign-in core.

Pure: tokens, one-time codes, and the orchestration service against an
in-memory store plus a fake gateway. No database, no routes, no HTTP — those
are covered in ``test_portal_auth_routes.py`` and in the integration suite.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

import jwt
import pytest
from app.portal import tokens
from app.portal.delivery import FakeSmsGateway
from app.portal.errors import (
    ExpiredInviteError,
    InvalidInviteError,
    InvalidSessionError,
    InvalidStepUpError,
    InviteAlreadyRedeemedError,
    SessionLifetimeExceededError,
    SessionRevokedError,
    TooManyAttemptsError,
)
from app.portal.otp import generate_otp, hash_otp, verify_otp
from app.portal.service import PortalAuthConfig, PortalAuthService
from app.portal.store import (
    InMemoryPortalAuthStore,
    InMemoryPortalSessionStore,
)

KEY = "test-signing-key-not-a-real-secret"
# A string that is not a token, named so it is never mistaken for a secret.
_NOT_A_TOKEN = "anything"
# Anchor the test clock to the real now so PyJWT's real-clock ``exp`` checks
# agree with the service's injected clock. Only the expiry test advances its
# own fake clock past this.
T0 = int(time.time())


def _invite_token(
    *,
    jti: str = "j1",
    patient_id: str = "pat-1",
    tenant: str = "tenant-a",
    purpose: str = "intake",
    issued_at: int = T0,
    ttl: int = 900,
) -> str:
    return tokens.mint_invite_token(
        signing_key=KEY,
        claims=tokens.InviteClaims(jti=jti, patient_id=patient_id, tenant=tenant, purpose=purpose),
        lifetime=tokens.TokenLifetime(issued_at=issued_at, ttl_seconds=ttl),
    )


def _session_token(
    *,
    jti: str = "s1",
    patient_id: str = "pat-1",
    tenant: str = "tenant-a",
    issued_at: int = T0,
    ttl: int = 3600,
) -> str:
    return tokens.mint_session_token(
        signing_key=KEY,
        claims=tokens.SessionClaims(jti=jti, patient_id=patient_id, tenant=tenant),
        lifetime=tokens.TokenLifetime(issued_at=issued_at, ttl_seconds=ttl),
    )


def _raw_session_payload(**overrides: object) -> dict[str, object]:
    """A session token's claim set, minus whatever the caller drops.

    Built from the module's own type constant rather than a literal, so a
    test that removes ``sub`` is testing the missing claim and not a typo in
    a hand-copied ``typ``.
    """
    payload: dict[str, object] = {
        "typ": tokens._SESSION_TYP,
        "jti": "s1",
        "sub": "pat-1",
        "tid": "tenant-a",
        "iat": T0,
        "exp": T0 + 3600,
    }
    payload.update(overrides)
    return payload


# ── tokens ──────────────────────────────────────────────────────────


def test_invite_token_round_trips() -> None:
    claims = tokens.verify_invite_token(signing_key=KEY, token=_invite_token())
    assert claims.jti == "j1"
    assert claims.patient_id == "pat-1"
    assert claims.tenant == "tenant-a"
    assert claims.purpose == "intake"


def test_expired_invite_token_raises() -> None:
    tok = _invite_token(issued_at=T0 - 1000)  # exp = T0-100, already past
    with pytest.raises(ExpiredInviteError):
        tokens.verify_invite_token(signing_key=KEY, token=tok)


def test_tampered_or_wrong_key_invite_raises() -> None:
    tok = _invite_token()
    with pytest.raises(InvalidInviteError):
        tokens.verify_invite_token(signing_key=KEY + "-but-different", token=tok)


def test_session_token_cannot_be_verified_as_invite() -> None:
    """Token-type confusion guard: a session token is not an invite token."""
    session = _session_token()
    with pytest.raises(InvalidInviteError):
        tokens.verify_invite_token(signing_key=KEY, token=session)


def test_invite_token_cannot_be_verified_as_session() -> None:
    invite = _invite_token()
    with pytest.raises(InvalidSessionError):
        tokens.verify_session_token(signing_key=KEY, token=invite)


def test_session_token_round_trips() -> None:
    claims = tokens.verify_session_token(signing_key=KEY, token=_session_token())
    assert claims.jti == "s1"
    assert claims.patient_id == "pat-1"
    assert claims.tenant == "tenant-a"


def test_expired_session_token_raises() -> None:
    tok = _session_token(issued_at=T0 - 4000, ttl=3600)  # exp = T0-400, already past
    with pytest.raises(InvalidSessionError):
        tokens.verify_session_token(signing_key=KEY, token=tok)


def test_session_token_wrong_signing_key_raises() -> None:
    tok = _session_token()
    with pytest.raises(InvalidSessionError):
        tokens.verify_session_token(signing_key=KEY + "-but-different", token=tok)


def test_invite_token_replayed_as_session_raises() -> None:
    """Same guard as ``test_invite_token_cannot_be_verified_as_session``,
    named for the replay scenario: a leaked magic link must not double as a
    session credential."""
    invite = _invite_token()
    with pytest.raises(InvalidSessionError):
        tokens.verify_session_token(signing_key=KEY, token=invite)


@pytest.mark.parametrize("dropped", ["sub", "tid", "jti"])
def test_a_session_token_missing_any_claim_raises(dropped: str) -> None:
    """``jti`` is the sharpest of the three: a session token that names no
    revocation row could never be revoked, so refuse it rather than treat it
    as un-revocable."""
    payload = _raw_session_payload()
    del payload[dropped]
    tok = jwt.encode(payload, KEY, algorithm="HS256")

    with pytest.raises(InvalidSessionError):
        tokens.verify_session_token(signing_key=KEY, token=tok)


# ── one-time codes ──────────────────────────────────────────────────


def test_generate_otp_shape() -> None:
    otp = generate_otp(6)
    assert len(otp) == 6
    assert otp.isdigit()


def test_generate_otp_refuses_a_length_worth_guessing() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        generate_otp(3)


def test_otp_hash_verify() -> None:
    otp = "123456"
    h = hash_otp(otp, pepper=KEY)
    assert h != otp  # never store the code itself
    assert verify_otp("123456", otp_hash=h, pepper=KEY) is True
    assert verify_otp("654321", otp_hash=h, pepper=KEY) is False
    assert verify_otp("123456", otp_hash=h, pepper="wrong-pepper") is False


# ── service ─────────────────────────────────────────────────────────


def _service(sms: FakeSmsGateway, store: InMemoryPortalAuthStore) -> PortalAuthService:
    return PortalAuthService(
        config=PortalAuthConfig(signing_key=KEY),
        store=store,
        sms=sms,
        now=lambda: T0,
    )


def test_service_refuses_empty_signing_key() -> None:
    with pytest.raises(ValueError, match="signing key"):
        PortalAuthService(
            config=PortalAuthConfig(signing_key=""),
            store=InMemoryPortalAuthStore(),
            sms=FakeSmsGateway(),
            now=lambda: T0,
        )


def test_issue_invite_texts_code_and_returns_token() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = _service(sms, store)

    issued = svc.issue_invite(patient_id="pat-1", tenant="tenant-a", phone="+15005550006")

    assert issued.token
    assert len(sms.sent) == 1
    assert sms.sent[0].to == "+15005550006"
    # The body carries a six-digit code and no patient identifier.
    assert "verification code" in sms.sent[0].body
    assert "pat-1" not in sms.sent[0].body


def test_issue_invite_delivery_failure_leaves_no_orphaned_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the send raises, ``issue_invite`` must not have persisted a
    challenge the caller has no way to redeem — they never got the matching
    token back."""
    fixed_uuid = uuid.UUID(int=0)
    monkeypatch.setattr("app.portal.service.uuid4", lambda: fixed_uuid)

    class RaisingSmsGateway:
        def check_ready(self) -> None:
            return None

        def send(self, *, to: str, body: str) -> None:
            raise RuntimeError("delivery failed")

    store = InMemoryPortalAuthStore()
    svc = PortalAuthService(
        config=PortalAuthConfig(signing_key=KEY),
        store=store,
        sms=RaisingSmsGateway(),
        now=lambda: T0,
    )

    with pytest.raises(RuntimeError):
        svc.issue_invite(patient_id="pat-1", tenant="tenant-a", phone="+15005550006")

    assert store.get_challenge(fixed_uuid.hex) is None


def _issue_and_extract_otp(svc: PortalAuthService, sms: FakeSmsGateway) -> tuple[str, str]:
    issued = svc.issue_invite(patient_id="pat-1", tenant="tenant-a", phone="+15005550006")
    # Pull the code back out of the (fake) message the patient would have got.
    digits = "".join(c for c in sms.sent[-1].body if c.isdigit())
    return issued.token, digits[:6]


def test_redeem_happy_path_mints_session() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = _service(sms, store)
    token, otp = _issue_and_extract_otp(svc, sms)

    session = svc.redeem(token=token, otp=otp)

    assert session.patient_id == "pat-1"
    assert session.tenant == "tenant-a"
    claims = tokens.verify_session_token(signing_key=KEY, token=session.session_token)
    assert claims.patient_id == "pat-1"


def test_single_use_invite_cannot_be_redeemed_twice() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = _service(sms, store)
    token, otp = _issue_and_extract_otp(svc, sms)

    svc.redeem(token=token, otp=otp)
    with pytest.raises(InviteAlreadyRedeemedError):
        svc.redeem(token=token, otp=otp)


def test_wrong_otp_rejected_then_correct_code_still_works() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = _service(sms, store)
    token, otp = _issue_and_extract_otp(svc, sms)
    bad = "999999" if otp != "999999" else "111111"

    # A single wrong attempt is recorded but does not lock the invitation.
    with pytest.raises(InvalidStepUpError):
        svc.redeem(token=token, otp=bad)
    jti = tokens.verify_invite_token(signing_key=KEY, token=token).jti
    challenge = store.get_challenge(jti)
    assert challenge is not None
    assert challenge.attempts == 1

    # The legitimate code still redeems.
    session = svc.redeem(token=token, otp=otp)
    assert session.patient_id == "pat-1"


def test_attempts_lock_out_after_cap() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = PortalAuthService(
        config=PortalAuthConfig(signing_key=KEY, max_otp_attempts=3),
        store=store,
        sms=sms,
        now=lambda: T0,
    )
    token, otp = _issue_and_extract_otp(svc, sms)

    for _ in range(3):
        with pytest.raises(InvalidStepUpError):
            svc.redeem(token=token, otp="111111")
    # Cap reached — even the correct code is now refused.
    with pytest.raises(TooManyAttemptsError):
        svc.redeem(token=token, otp=otp)


def test_redeem_after_challenge_expiry_raises() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    clock = {"t": T0}
    svc = PortalAuthService(
        config=PortalAuthConfig(signing_key=KEY, invite_ttl_seconds=900),
        store=store,
        sms=sms,
        now=lambda: clock["t"],
    )
    token, otp = _issue_and_extract_otp(svc, sms)
    clock["t"] = T0 + 901  # past the challenge expiry

    with pytest.raises(ExpiredInviteError):
        svc.redeem(token=token, otp=otp)


def test_redeem_unknown_token_raises() -> None:
    sms, store = FakeSmsGateway(), InMemoryPortalAuthStore()
    svc = _service(sms, store)
    # A well-formed token whose challenge was never stored.
    orphan = _invite_token(jti="ghost")
    with pytest.raises(InvalidInviteError):
        svc.redeem(token=orphan, otp="123456")


# ── sessions: recording, rotation, revocation ───────────────────────────


class _Clock:
    """A hand-wound clock, so TTL and ceiling assertions are exact."""

    def __init__(self, now: int = T0) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


def _full_service(
    sms: FakeSmsGateway,
    store: InMemoryPortalAuthStore,
    sessions: InMemoryPortalSessionStore,
    clock: _Clock,
    *,
    max_lifetime: int = 2_592_000,
) -> PortalAuthService:
    return PortalAuthService(
        config=PortalAuthConfig(signing_key=KEY, session_max_lifetime_seconds=max_lifetime),
        store=store,
        sessions=sessions,
        sms=sms,
        now=clock,
    )


def _parts() -> tuple[FakeSmsGateway, InMemoryPortalAuthStore, InMemoryPortalSessionStore, _Clock]:
    return (
        FakeSmsGateway(),
        InMemoryPortalAuthStore(),
        InMemoryPortalSessionStore(),
        _Clock(),
    )


def _redeemed(svc: PortalAuthService, sms: FakeSmsGateway) -> tuple[str, str]:
    """Issue, redeem, and hand back ``(session_token, session_jti)``."""
    token, otp = _issue_and_extract_otp(svc, sms)
    session = svc.redeem(token=token, otp=otp)
    return session.session_token, session.jti


def test_redeem_records_the_row_that_makes_a_session_revocable() -> None:
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)

    _token, jti = _redeemed(svc, sms)

    record = sessions.get(jti)
    assert record is not None
    assert record.patient_id == "pat-1"
    assert record.chain_started_at == T0
    assert record.is_live(now=T0) is True


def test_refresh_rotates_the_jti_and_retires_the_old_row() -> None:
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    first_token, first_jti = _redeemed(svc, sms)

    clock.advance(60)
    rotated = svc.refresh(token=first_token)

    assert rotated.jti != first_jti
    old = sessions.get(first_jti)
    assert old is not None
    assert old.revoked_at == T0 + 60
    assert sessions.get(rotated.jti) is not None


def test_refresh_carries_the_chain_start_forward() -> None:
    """The ceiling is measured from the ORIGINAL redemption, so it cannot be
    pushed out one rotation at a time.

    The clock stays at ``T0`` here: PyJWT checks a token's own ``iat`` and
    ``exp`` against the REAL clock, so a fake clock that runs ahead mints
    tokens PyJWT calls immature. Two rotations at the same instant still
    prove the property — what is asserted is that the chain start is copied,
    not recomputed.
    """
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    first_token, first_jti = _redeemed(svc, sms)
    # Backdate the chain so "copied forward" is distinguishable from "set to
    # now" — with both equal to T0 the assertion would pass either way.
    started_long_ago = T0 - 100_000
    original = sessions.get(first_jti)
    assert original is not None
    sessions.record(replace(original, chain_started_at=started_long_ago))

    second = svc.refresh(token=first_token)
    third = svc.refresh(token=second.session_token)

    record = sessions.get(third.jti)
    assert record is not None
    assert record.chain_started_at == started_long_ago


def test_refresh_refuses_past_the_chain_ceiling() -> None:
    """Sliding renewal ends somewhere: past the ceiling the patient proves
    both factors again rather than riding one redemption forever.

    Aged by backdating the chain in the store rather than by winding the
    clock forward, so the token stays valid to PyJWT and the ONLY thing
    refusing it is the ceiling.
    """
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock, max_lifetime=7_200)
    token, _jti = _redeemed(svc, sms)

    # Inside the ceiling: rotation works.
    rotated = svc.refresh(token=token)
    record = sessions.get(rotated.jti)
    assert record is not None

    # Same chain, now older than the ceiling.
    sessions.record(replace(record, chain_started_at=T0 - 7_201))
    with pytest.raises(SessionLifetimeExceededError):
        svc.refresh(token=rotated.session_token)


def test_a_rotated_away_session_cannot_refresh_again() -> None:
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    first_token, _ = _redeemed(svc, sms)
    svc.refresh(token=first_token)

    with pytest.raises(SessionRevokedError):
        svc.refresh(token=first_token)


def test_refresh_refuses_a_row_that_names_another_patient() -> None:
    """Signature good, row present, and the two disagree about who this is.
    The token's claim is the half an attacker would control."""
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    token, jti = _redeemed(svc, sms)
    record = sessions.get(jti)
    assert record is not None
    sessions.record(replace(record, patient_id="someone-else"))

    with pytest.raises(SessionRevokedError):
        svc.refresh(token=token)


def test_revoke_patient_stops_every_live_session() -> None:
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    first_token, _ = _redeemed(svc, sms)
    second_token, _ = _redeemed(svc, sms)

    assert svc.revoke_patient(patient_id="pat-1") == 2

    for token in (first_token, second_token):
        with pytest.raises(SessionRevokedError):
            svc.refresh(token=token)


def test_a_session_whose_row_has_expired_cannot_be_refreshed() -> None:
    """The row is the authority, not the token.

    A token can still be inside its own ``exp`` while the session it names
    has been aged out server-side — after a clock skew, or once expiry is
    shortened. Refresh follows the row.
    """
    sms, store, sessions, clock = _parts()
    svc = _full_service(sms, store, sessions, clock)
    token, jti = _redeemed(svc, sms)
    record = sessions.get(jti)
    assert record is not None
    sessions.record(replace(record, expires_at=T0 - 1))

    with pytest.raises(SessionRevokedError):
        svc.refresh(token=token)


def test_a_service_without_a_session_store_refuses_to_pretend() -> None:
    """No revocation list means a minted token could never be called back.
    Refuse the operations that claim to revoke rather than silently succeed
    at nothing."""
    svc = _service(FakeSmsGateway(), InMemoryPortalAuthStore())

    with pytest.raises(ValueError, match="session store"):
        svc.revoke_patient(patient_id="pat-1")
    with pytest.raises(ValueError, match="session store"):
        svc.refresh(token=_NOT_A_TOKEN)
