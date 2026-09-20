# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""The portal front door on the patient-principal seam.

Two halves, tested in two places. The stateless half — signature, token
type, practice claim — and the composition around the row lookup are here.
The row lookup itself runs against Postgres in
``backend/tests_integration/database/test_portal_auth_store.py``.

The properties that matter most are the ones the protocol asks resolver
authors to guarantee, and neither is self-evident from reading ``resolve``:

* **Rejection is ``None``, never an exception.** A resolver that raises
  aborts resolution for EVERY front door behind it, which is an
  auth-strength downgrade waiting to happen once a second door exists.
* **A clinician credential must fail this verifier structurally**, not by
  this code remembering to check for one.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
)
from app.portal import tokens
from app.portal.resolver import (
    CREDENTIAL_KIND,
    PORTAL_SESSION_KIND,
    PortalSessionResolver,
)
from app.portal.store import PortalSessionRecord

KEY = "resolver-test-signing-key-not-a-real-secret"
TENANT = "practice_abc123"
PATIENT_ID = "11111111-1111-4111-8111-111111111111"
NOW = int(time.time())


class _StubbedResolver(PortalSessionResolver):
    """The real resolver with only the database reach stubbed out."""

    def __init__(self, record: PortalSessionRecord | None) -> None:
        self._record = record
        self.looked_up: list[str] = []

    def _signing_key(self) -> str:
        return KEY

    def _live_record(self, claims: tokens.SessionClaims) -> PortalSessionRecord | None:
        self.looked_up.append(claims.jti)
        if self._record is None or not self._record.is_live(now=int(time.time())):
            return None
        return self._record


def _live_record(*, jti: str = "s1", patient_id: str = PATIENT_ID) -> PortalSessionRecord:
    return PortalSessionRecord(
        jti=jti,
        patient_id=patient_id,
        issued_at=NOW,
        expires_at=NOW + 3600,
        chain_started_at=NOW,
    )


def _session_token(
    *,
    jti: str = "s1",
    patient_id: str = PATIENT_ID,
    tenant: str = TENANT,
    signing_key: str = KEY,
    ttl: int = 3600,
) -> str:
    return tokens.mint_session_token(
        signing_key=signing_key,
        claims=tokens.SessionClaims(jti=jti, patient_id=patient_id, tenant=tenant),
        lifetime=tokens.TokenLifetime(issued_at=NOW, ttl_seconds=ttl),
    )


def _credential(token: str) -> PatientCredential:
    return PatientCredential(kind=CREDENTIAL_KIND, value=token)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_live_session_resolves_to_its_patient() -> None:
    resolver = _StubbedResolver(_live_record())

    context = resolver.resolve(_credential(_session_token()))

    assert context == PatientContext(
        patient_id=PATIENT_ID,
        practice_schema=TENANT,
        credential_kind=PORTAL_SESSION_KIND,
        auth_strength=AuthStrength.STEPPED_UP,
        session_id="s1",
    )


def test_the_session_handle_comes_from_the_row_not_the_token() -> None:
    """The handle a sign-out acts on must be the one the ROW carried.

    Both halves name a session and they agree here, because the row was
    found by the token's claim. Taking it off the record anyway is what
    means a route that retires ``session_id`` can never be handed a handle
    the caller chose — and it is the reason this is worth asserting rather
    than reading off the same claim twice.
    """
    resolver = _StubbedResolver(_live_record(jti="row-handle"))

    context = resolver.resolve(_credential(_session_token(jti="row-handle")))

    assert context is not None
    assert context.session_id == "row-handle"


def test_the_principal_records_that_two_factors_were_cleared() -> None:
    """A session only exists because a redemption cleared link possession AND
    the texted code. Routes that gate on ``STEPPED_UP`` are relying on this,
    so it is asserted rather than implied."""
    resolver = _StubbedResolver(_live_record())

    context = resolver.resolve(_credential(_session_token()))

    assert context is not None
    assert context.auth_strength is AuthStrength.STEPPED_UP


def test_the_schema_comes_from_the_token_claim() -> None:
    resolver = _StubbedResolver(_live_record())

    context = resolver.resolve(_credential(_session_token(tenant="practice_other")))

    assert context is not None
    assert context.practice_schema == "practice_other"


# ---------------------------------------------------------------------------
# Refusals — all of them None, none of them raising
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("garbage", "not-a-token-at-all"),
        ("empty", " "),
        ("wrong-key", _session_token(signing_key="some-other-key")),
        ("expired", _session_token(ttl=-60)),
    ],
)
def test_a_bad_token_resolves_to_none_without_raising(label: str, token: str) -> None:
    resolver = _StubbedResolver(_live_record())

    assert resolver.resolve(_credential(token)) is None
    # It never even reached the row lookup.
    assert resolver.looked_up == []


def test_an_invite_token_is_not_a_session_token() -> None:
    """Type confusion is the failure this guards: a magic link is ONE factor,
    and must never be spendable as the credential that two factors earn."""
    invite = tokens.mint_invite_token(
        signing_key=KEY,
        claims=tokens.InviteClaims(
            jti="i1", patient_id=PATIENT_ID, tenant=TENANT, purpose="intake"
        ),
        lifetime=tokens.TokenLifetime(issued_at=NOW, ttl_seconds=900),
    )
    resolver = _StubbedResolver(_live_record())

    assert resolver.resolve(_credential(invite)) is None


@pytest.mark.parametrize("tenant", ["platform", "public", "practice"])
def test_a_non_practice_schema_claim_is_refused(tenant: str) -> None:
    """The shared template and the platform schema are valid identifiers and
    wrong answers. ``get_patient_context`` checks this too; a resolver that
    returns one is exactly what that check exists to catch."""
    resolver = _StubbedResolver(_live_record())

    assert resolver.resolve(_credential(_session_token(tenant=tenant))) is None
    assert resolver.looked_up == []


def test_a_revoked_session_resolves_to_none() -> None:
    """The half a stateless verifier cannot do. The token is perfectly valid;
    the row says access ended."""
    revoked = PortalSessionRecord(
        jti="s1",
        patient_id=PATIENT_ID,
        issued_at=NOW,
        expires_at=NOW + 3600,
        chain_started_at=NOW,
        revoked_at=NOW + 1,
    )
    resolver = _StubbedResolver(revoked)

    assert resolver.resolve(_credential(_session_token())) is None


def test_a_missing_row_resolves_to_none() -> None:
    """A token whose session was rotated away by a refresh, or whose row
    never existed."""
    resolver = _StubbedResolver(None)

    assert resolver.resolve(_credential(_session_token())) is None


def test_a_row_naming_a_different_patient_is_refused() -> None:
    """Signature good, row present, and the two disagree about who this is.
    Nothing legitimate produces it, so refuse rather than pick one."""
    resolver = _StubbedResolver(_live_record(patient_id="99999999-9999-4999-8999-999999999999"))

    assert resolver.resolve(_credential(_session_token())) is None


def test_an_empty_signing_key_refuses_everything() -> None:
    """An unset key would check every signature against nothing."""

    class _Unconfigured(_StubbedResolver):
        def _signing_key(self) -> str:
            return ""

    assert _Unconfigured(_live_record()).resolve(_credential(_session_token())) is None


# ---------------------------------------------------------------------------
# Registry behaviour
# ---------------------------------------------------------------------------


def test_the_resolver_registers_under_the_bearer_scheme() -> None:
    """``credential_kind`` is the registry key, and the dependency normalizes
    the ``Authorization`` scheme to lowercase before lookup."""
    assert PortalSessionResolver.credential_kind == "bearer"


def test_a_registered_resolver_is_reached_through_the_registry() -> None:
    registry = PatientResolverRegistry()
    registry.register(_StubbedResolver(_live_record()))

    context = registry.resolve(_credential(_session_token()))

    assert context is not None
    assert context.credential_kind == PORTAL_SESSION_KIND


def test_refusing_with_none_lets_a_later_front_door_answer() -> None:
    """Why the contract says ``None``, not an exception: a resolver that
    raised would abort resolution for the doors behind it. This is the
    arrangement that breaks if this resolver ever starts raising."""

    class _SecondDoor:
        credential_kind = CREDENTIAL_KIND

        def resolve(self, credential: PatientCredential) -> PatientContext | None:
            return PatientContext(
                patient_id="from-the-other-door",
                practice_schema=TENANT,
                credential_kind="some_other_front_door",
                auth_strength=AuthStrength.SINGLE_FACTOR,
            )

    registry = PatientResolverRegistry()
    registry.register(_StubbedResolver(None))  # refuses
    registry.register(_SecondDoor())

    context = registry.resolve(_credential(_session_token()))

    assert context is not None
    assert context.patient_id == "from-the-other-door"


def test_a_clinician_shaped_token_is_refused_by_the_verifier() -> None:
    """The structural separation the protocol requires.

    A clinician's identity-provider JWT arrives under the same ``bearer``
    scheme, so it reaches this resolver. It is refused by the signature check
    — the provider signs RS256 against its own keys — rather than by any
    check that looks for "a clinician token", which is what keeps the
    guarantee from depending on remembering to write one.
    """
    provider_shaped = pyjwt.encode(
        {
            "iss": "https://securetoken.example.test/pablo-dev",
            "aud": "pablo-dev",
            "sub": "provider-uid-123",
            "email": "clinician@example.test",
            "iat": NOW,
            "exp": NOW + 3600,
        },
        KEY,
        algorithm="HS256",
    )
    resolver = _StubbedResolver(_live_record())

    # Even signed with OUR key (the most generous case for an attacker), it
    # has no portal ``typ`` and no portal claims.
    assert resolver.resolve(_credential(provider_shaped)) is None
