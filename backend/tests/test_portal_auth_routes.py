# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""HTTP tests for the portal credential lifecycle.

Mounts the real router on a fresh FastAPI app with auth, the stores and both
delivery channels overridden. The handlers under test are the real ones —
what is swapped out is Postgres and the two providers.

What these tests are FOR, in order of how much they would hurt to get wrong:

* **No credential in a response.** The invite route must never return the
  token, and the clinician must never be able to read it back out of the
  access-state route either.
* **A uniform 401.** Six distinct redemption failures have to be
  indistinguishable on the wire, or the endpoint is an oracle — and
  indistinguishable from the rest of the patient surface too.
* **Revocation actually revokes**, including the invitation still in flight.
* **Rotation retires what it replaces.**

The database-backed stores are proven separately, against a freshly
provisioned practice schema, in
``backend/tests_integration/database/test_portal_auth_store.py``.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.patient_context import patient_not_authenticated_detail
from app.auth.service import get_current_user, require_active_subscription
from app.models.audit import AuditAction
from app.portal import tokens
from app.portal.delivery import CapturingInviteDelivery, DeliveryNotConfigured, FakeSmsGateway
from app.portal.factory import get_invite_delivery, get_sms_gateway
from app.portal.practice_routes import PracticeAddress
from app.portal.routes import router
from app.portal.store import InMemoryPortalAuthStore, InMemoryPortalSessionStore
from app.portal.tenant_gateway import (
    PortalStores,
    PortalTenantWork,
    get_portal_stores,
    get_portal_tenant_gateway,
)
from app.rate_limit import reset_portal_limiters
from app.repositories import get_patient_repository
from app.services.audit_service import AuditService, get_audit_service
from app.settings import get_settings
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

TENANT = "practice_abc123"
SIGNING_KEY = "route-test-signing-key-not-a-real-secret"
PORTAL_ORIGIN = "https://portal.example.test"
PRACTICE_ID = "practice-1"
PRACTICE_SLUG = "example-therapy"
PRACTICE_NAME = "Example Therapy"

PATIENT_ID = "11111111-1111-4111-8111-111111111111"
NO_PHONE_PATIENT_ID = "22222222-2222-4222-8222-222222222222"
NO_EMAIL_PATIENT_ID = "33333333-3333-4333-8333-333333333333"
UNKNOWN_PATIENT_ID = "44444444-4444-4444-8444-444444444444"


class _FakePatient:
    def __init__(self, patient_id: str, email: str | None, phone: str | None) -> None:
        self.id = patient_id
        self.email = email
        self.phone = phone


_PATIENTS: dict[str, _FakePatient] = {
    PATIENT_ID: _FakePatient(PATIENT_ID, "patient@example.test", "+15005550006"),
    NO_PHONE_PATIENT_ID: _FakePatient(NO_PHONE_PATIENT_ID, "patient@example.test", None),
    NO_EMAIL_PATIENT_ID: _FakePatient(NO_EMAIL_PATIENT_ID, None, "+15005550006"),
}


class _FakePatientRepository:
    """Just enough ``PatientRepository`` for the chart lookup.

    ``get`` takes the calling clinician's id and would consult their access
    grant; here every id in the map is reachable and everything else is not,
    which is the only distinction these tests turn on.
    """

    def get(self, patient_id: str, user_id: str) -> Any:
        return _PATIENTS.get(patient_id)


class _RecordingAudit:
    """Counts audit calls without a repository behind them."""

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.changes: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.actions.append(str(action))
        self.changes.append(kwargs.get("changes") or {})

    def log_patient_principal_action(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.actions.append(str(action))
        self.changes.append(kwargs.get("changes") or {})


class _FakeTenantGateway:
    """The patient-facing gateway, over in-memory stores.

    Records which practice it was asked for, because "the schema came from
    the token claim" is a property worth asserting rather than assuming.
    """

    def __init__(self, stores: PortalStores, audit: _RecordingAudit) -> None:
        self._stores = stores
        self._audit = audit
        self.opened: list[str] = []
        self.commits = 0

    @contextmanager
    def open(self, tenant: str, request: Request | None) -> Iterator[PortalTenantWork]:
        self.opened.append(tenant)

        def _commit() -> None:
            self.commits += 1

        def _record(patient_id: str, session_jti: str) -> None:
            self._audit.log_patient_principal_action(
                AuditAction.PATIENT_PORTAL_SESSION_REDEEMED,
                request,
                patient_id=patient_id,
                changes={"session_jti": session_jti},
            )

        yield PortalTenantWork(
            challenges=self._stores.challenges,
            sessions=self._stores.sessions,
            record_redemption=_record,
            commit=_commit,
        )


@pytest.fixture(autouse=True)
def _portal_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the service at a test signing key and a portal origin.

    ``get_settings`` is cached, so the cache is cleared on both sides of the
    test rather than left holding a patched object.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", PORTAL_ORIGIN)
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_rate_limits() -> Iterator[None]:
    """The limiters are module-level singletons; a test that spends a window
    must not charge it to the next one."""
    reset_portal_limiters()
    yield
    reset_portal_limiters()


@pytest.fixture(autouse=True)
def _practice_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the caller's practice a portal address, without a platform table.

    Both halves are patched rather than the helper that composes them, so the
    route still has to resolve the practice from the CALLER and then ask for
    that practice's address — which is the part worth keeping honest here.
    The directory's own behaviour is covered in
    ``test_portal_practice_routes.py``.
    """
    monkeypatch.setattr(
        "app.portal.routes._resolve_practice_from_email",
        lambda _email: (PRACTICE_ID, TENANT),
    )
    monkeypatch.setattr(
        "app.portal.routes.ensure_practice_slug",
        lambda practice_id: _address(
            PRACTICE_SLUG if practice_id == PRACTICE_ID else "wrong-practice"
        ),
    )
    monkeypatch.setattr(
        "app.portal.routes.practice_address_for_schema",
        lambda schema: _address(PRACTICE_SLUG) if schema == TENANT else None,
    )


def _address(slug: str, *, enabled: bool = True) -> PracticeAddress:
    return PracticeAddress(slug=slug, display_name=PRACTICE_NAME, enabled=enabled)


@pytest.fixture
def stores() -> PortalStores:
    return PortalStores(
        challenges=InMemoryPortalAuthStore(),
        sessions=InMemoryPortalSessionStore(),
        tenant=TENANT,
    )


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def sms() -> FakeSmsGateway:
    return FakeSmsGateway()


@pytest.fixture
def delivery() -> CapturingInviteDelivery:
    return CapturingInviteDelivery()


@pytest.fixture
def gateway(stores: PortalStores, audit: _RecordingAudit) -> _FakeTenantGateway:
    return _FakeTenantGateway(stores, audit)


@pytest.fixture
def app(
    mock_user: User,
    stores: PortalStores,
    audit: _RecordingAudit,
    sms: FakeSmsGateway,
    delivery: CapturingInviteDelivery,
    gateway: _FakeTenantGateway,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)
    application.dependency_overrides[get_current_user] = lambda: mock_user
    application.dependency_overrides[require_active_subscription] = lambda: mock_user
    application.dependency_overrides[get_patient_repository] = _FakePatientRepository
    application.dependency_overrides[get_portal_stores] = lambda: stores
    application.dependency_overrides[get_portal_tenant_gateway] = lambda: gateway
    application.dependency_overrides[get_audit_service] = lambda: audit
    application.dependency_overrides[get_invite_delivery] = lambda: delivery
    application.dependency_overrides[get_sms_gateway] = lambda: sms
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _invite_url(patient_id: str = PATIENT_ID) -> str:
    return f"/api/patients/{patient_id}/portal-invite"


def _access_url(patient_id: str = PATIENT_ID) -> str:
    return f"/api/patients/{patient_id}/portal-access"


def _otp_from(sms: FakeSmsGateway) -> str:
    digits = "".join(c for c in sms.sent[-1].body if c.isdigit())
    return digits[:6]


def _link_token(delivery: CapturingInviteDelivery) -> str:
    """The token as the patient would extract it from the magic link."""
    link = delivery.sent[-1].link
    return link.split("#token=", 1)[1]


def _issue(client: TestClient, patient_id: str = PATIENT_ID) -> Any:
    return client.post(_invite_url(patient_id))


# ---------------------------------------------------------------------------
# Issuing an invitation
# ---------------------------------------------------------------------------


def test_invite_returns_202_and_no_credential(
    client: TestClient, delivery: CapturingInviteDelivery, sms: FakeSmsGateway
) -> None:
    response = _issue(client)

    assert response.status_code == 202
    body = response.json()
    assert body["patient_id"] == PATIENT_ID
    assert body["invite_expires_at"] > int(time.time())

    # The credential exists — it just is not in the response. Asserted
    # against the serialized body so a future field that happens to carry
    # the token (or any part of it) fails here.
    raw = response.text
    token = _link_token(delivery)
    assert token not in raw
    assert _otp_from(sms) not in raw


def test_invite_emails_only_the_link(client: TestClient, delivery: CapturingInviteDelivery) -> None:
    _issue(client)

    assert len(delivery.sent) == 1
    sent = delivery.sent[0]
    assert sent.to_email == "patient@example.test"
    assert sent.link.startswith(f"{PORTAL_ORIGIN}/portal/{PRACTICE_SLUG}#token=")
    # The token rides in the FRAGMENT, which is never sent to a server — a
    # "?token=" link would be logged by every hop that handled it.
    assert "?token=" not in sent.link


def test_invite_texts_only_the_code(client: TestClient, sms: FakeSmsGateway) -> None:
    _issue(client)

    assert len(sms.sent) == 1
    body = sms.sent[0].body
    assert _otp_from(sms) in body
    # No patient identifier, no name, nothing clinical.
    assert PATIENT_ID not in body
    assert "patient@example.test" not in body


def test_invite_audits_with_a_token_handle_and_nothing_else(
    client: TestClient, audit: _RecordingAudit, delivery: CapturingInviteDelivery
) -> None:
    _issue(client)

    assert audit.actions == [AuditAction.PATIENT_PORTAL_INVITE_ISSUED.value]
    changes = audit.changes[0]
    assert set(changes) == {"invite_jti"}
    assert changes["invite_jti"] not in _link_token(delivery)


@pytest.mark.parametrize(
    "patient_id", [NO_PHONE_PATIENT_ID, NO_EMAIL_PATIENT_ID], ids=["no-phone", "no-email"]
)
def test_invite_422s_without_both_channels(
    client: TestClient,
    patient_id: str,
    sms: FakeSmsGateway,
    delivery: CapturingInviteDelivery,
) -> None:
    """One channel is one factor. Refuse rather than ship a two-factor flow
    with one factor missing — and send nothing on the way out."""
    response = _issue(client, patient_id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PATIENT_CONTACT_INCOMPLETE"
    assert sms.sent == []
    assert delivery.sent == []


def test_invite_409s_when_the_practice_turned_its_portal_off(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    sms: FakeSmsGateway,
    delivery: CapturingInviteDelivery,
) -> None:
    """An invitation to a page that answers 404 is worse than no invitation:
    the clinician believes it was sent and the patient is the one who finds
    out. Refused before anything is minted or sent."""
    monkeypatch.setattr(
        "app.portal.routes.ensure_practice_slug",
        lambda _practice_id: _address(PRACTICE_SLUG, enabled=False),
    )

    response = TestClient(app).post(_invite_url())

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PORTAL_DISABLED_FOR_PRACTICE"
    assert sms.sent == []
    assert delivery.sent == []


def test_invite_404s_for_unknown_patient(client: TestClient) -> None:
    assert _issue(client, UNKNOWN_PATIENT_ID).status_code == 404


def test_invite_503s_when_a_channel_is_unconfigured_and_sends_nothing(
    app: FastAPI, stores: PortalStores, sms: FakeSmsGateway
) -> None:
    """The route must refuse loudly — and, crucially, BEFORE texting a code
    for a link that will never arrive."""
    app.dependency_overrides[get_invite_delivery] = lambda: DeliveryNotConfigured("email")
    response = TestClient(app).post(_invite_url())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PORTAL_DELIVERY_NOT_CONFIGURED"
    assert sms.sent == []
    assert stores.challenges.has_outstanding(PATIENT_ID) is False


def test_invite_503s_when_the_step_up_channel_is_unconfigured(
    app: FastAPI, sms: FakeSmsGateway
) -> None:
    app.dependency_overrides[get_sms_gateway] = lambda: DeliveryNotConfigured("SMS")
    response = TestClient(app).post(_invite_url())

    assert response.status_code == 503
    assert sms.sent == []


def test_invite_503s_when_no_portal_origin_is_configured(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nowhere for the link to point there is no link to mint."""
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "")
    response = TestClient(app).post(_invite_url())

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Redeeming — the uniform 401
# ---------------------------------------------------------------------------

REDEEM_URL = "/api/patient/auth/redeem"
REFRESH_URL = "/api/patient/auth/refresh"


def _redeem(client: TestClient, token: str, otp: str) -> Any:
    return client.post(REDEEM_URL, json={"token": token, "otp": otp})


def _issue_and_capture(
    client: TestClient, delivery: CapturingInviteDelivery, sms: FakeSmsGateway
) -> tuple[str, str]:
    _issue(client)
    return _link_token(delivery), _otp_from(sms)


def test_redeem_mints_a_session_carrying_a_token_handle(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    stores: PortalStores,
) -> None:
    token, otp = _issue_and_capture(client, delivery, sms)

    response = _redeem(client, token, otp)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    claims = tokens.verify_session_token(signing_key=SIGNING_KEY, token=body["session_token"])
    assert claims.patient_id == PATIENT_ID
    assert claims.tenant == TENANT
    assert claims.jti
    # The row that makes it revocable exists.
    assert stores.sessions.get(claims.jti) is not None


def test_a_minted_session_says_which_practice_page_it_belongs_to(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
) -> None:
    """A magic link carries the address in its path, so the ordinary caller
    already knows. These fields are for anything that arrives without one, and
    they are on both routes so a rotation does not lose the answer."""
    token, otp = _issue_and_capture(client, delivery, sms)

    redeemed = _redeem(client, token, otp).json()

    assert redeemed["practice_slug"] == PRACTICE_SLUG
    assert redeemed["practice_display_name"] == PRACTICE_NAME

    rotated = _refresh(client, redeemed["session_token"]).json()

    assert rotated["practice_slug"] == PRACTICE_SLUG
    assert rotated["practice_display_name"] == PRACTICE_NAME


def test_a_session_still_mints_when_the_practice_has_no_address_yet(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential is already committed by the time the address is read, so
    a practice without one — or a platform hiccup reading it — must not turn a
    completed sign-in into the uniform 401."""
    token, otp = _issue_and_capture(client, delivery, sms)
    monkeypatch.setattr(
        "app.portal.routes.practice_address_for_schema",
        lambda _schema: (_ for _ in ()).throw(RuntimeError("platform is having a moment")),
    )

    response = _redeem(client, token, otp)

    assert response.status_code == 200
    body = response.json()
    assert body["session_token"]
    assert body["practice_slug"] is None
    assert body["practice_display_name"] is None


def test_redeem_enters_the_practice_from_the_token_claim(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    gateway: _FakeTenantGateway,
) -> None:
    token, otp = _issue_and_capture(client, delivery, sms)
    _redeem(client, token, otp)

    assert gateway.opened == [TENANT]


def test_redeem_audits_the_authentication(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    audit: _RecordingAudit,
) -> None:
    """§ 164.312(b): a completed authentication that nothing recorded is the
    one failure this path cannot have."""
    token, otp = _issue_and_capture(client, delivery, sms)
    _redeem(client, token, otp)

    assert AuditAction.PATIENT_PORTAL_SESSION_REDEEMED.value in audit.actions


def _uniform_401(response: Any) -> bool:
    return response.status_code == 401 and response.json() == {
        "detail": patient_not_authenticated_detail()
    }


def test_the_refusal_is_the_one_the_rest_of_the_patient_surface_gives(
    client: TestClient,
) -> None:
    """Not a tautology against ``_uniform_401``: this asserts the literal
    body, so a change to the shared helper that quietly altered the wording
    has to be made deliberately here too."""
    response = _redeem(client, "not-a-token", "123456")

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "error": {
                "code": "PATIENT_NOT_AUTHENTICATED",
                "message": "Not authenticated",
                "details": {},
            }
        }
    }


def test_every_redemption_failure_is_the_same_401(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    stores: PortalStores,
) -> None:
    """The oracle test. Six different reasons, one indistinguishable answer —
    status AND body, asserted together, because a caller can see both and a
    difference in either is a signal."""
    refusals = []

    # 1. Garbage that was never a token.
    refusals.append(_redeem(client, "not-a-token", "123456"))

    # 2. A token signed with the wrong key.
    forged = tokens.mint_invite_token(
        signing_key="a-different-key",
        claims=tokens.InviteClaims(
            jti="forged", patient_id=PATIENT_ID, tenant=TENANT, purpose="intake"
        ),
        lifetime=tokens.TokenLifetime(issued_at=int(time.time()), ttl_seconds=900),
    )
    refusals.append(_redeem(client, forged, "123456"))

    # 3. A well-signed token whose tenant claim is not a practice schema.
    wrong_tenant = tokens.mint_invite_token(
        signing_key=SIGNING_KEY,
        claims=tokens.InviteClaims(
            jti="x", patient_id=PATIENT_ID, tenant="platform", purpose="intake"
        ),
        lifetime=tokens.TokenLifetime(issued_at=int(time.time()), ttl_seconds=900),
    )
    refusals.append(_redeem(client, wrong_tenant, "123456"))

    # 4. A real invitation, wrong code.
    token, otp = _issue_and_capture(client, delivery, sms)
    wrong = "000000" if otp != "000000" else "111111"
    refusals.append(_redeem(client, token, wrong))

    # 5. The same invitation, after it was redeemed (single-use).
    assert _redeem(client, token, otp).status_code == 200
    refusals.append(_redeem(client, token, otp))

    # 6. An invitation whose challenge was burned by a clinician revoke.
    token2, otp2 = _issue_and_capture(client, delivery, sms)
    stores.challenges.consume_outstanding(PATIENT_ID)
    refusals.append(_redeem(client, token2, otp2))

    assert [r.status_code for r in refusals] == [401] * 6
    assert all(_uniform_401(r) for r in refusals)


def test_attempt_cap_burns_the_invitation_and_persists_the_count(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    stores: PortalStores,
    gateway: _FakeTenantGateway,
) -> None:
    """A wrong code has to COST something. The attempt bump is committed on
    the failure path, or guessing is free."""
    token, otp = _issue_and_capture(client, delivery, sms)
    wrong = "000000" if otp != "000000" else "111111"
    jti = tokens.verify_invite_token(signing_key=SIGNING_KEY, token=token).jti

    commits_before = gateway.commits
    for _ in range(5):
        assert _redeem(client, token, wrong).status_code == 401

    challenge = stores.challenges.get_challenge(jti)
    assert challenge is not None
    assert challenge.attempts == 5
    assert gateway.commits == commits_before + 5

    # Capped: even the RIGHT code no longer works.
    assert _uniform_401(_redeem(client, token, otp))


# ---------------------------------------------------------------------------
# Refresh — rotation
# ---------------------------------------------------------------------------


def _refresh(client: TestClient, session_token: str) -> Any:
    return client.post(REFRESH_URL, json={"session_token": session_token})


def test_refresh_rotates_the_token_and_retires_the_old_session(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    stores: PortalStores,
) -> None:
    token, otp = _issue_and_capture(client, delivery, sms)
    first = _redeem(client, token, otp).json()["session_token"]
    first_jti = tokens.verify_session_token(signing_key=SIGNING_KEY, token=first).jti

    response = _refresh(client, first)

    assert response.status_code == 200
    second = response.json()["session_token"]
    second_jti = tokens.verify_session_token(signing_key=SIGNING_KEY, token=second).jti
    assert second_jti != first_jti

    old = stores.sessions.get(first_jti)
    assert old is not None
    assert old.revoked_at is not None
    assert stores.sessions.get(second_jti) is not None


def test_a_rotated_away_session_cannot_refresh_again(
    client: TestClient, delivery: CapturingInviteDelivery, sms: FakeSmsGateway
) -> None:
    """The point of rotation: a token someone else copied stops working the
    moment the real patient renews."""
    token, otp = _issue_and_capture(client, delivery, sms)
    first = _redeem(client, token, otp).json()["session_token"]
    _refresh(client, first)

    assert _uniform_401(_refresh(client, first))


def test_refresh_carries_the_chain_start_forward(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    stores: PortalStores,
) -> None:
    """Sliding renewal is bounded from the ORIGINAL redemption, so the
    ceiling cannot be pushed out one rotation at a time."""
    token, otp = _issue_and_capture(client, delivery, sms)
    first = _redeem(client, token, otp).json()["session_token"]
    first_jti = tokens.verify_session_token(signing_key=SIGNING_KEY, token=first).jti
    original = stores.sessions.get(first_jti)
    assert original is not None

    second = _refresh(client, first).json()["session_token"]
    second_jti = tokens.verify_session_token(signing_key=SIGNING_KEY, token=second).jti

    rotated = stores.sessions.get(second_jti)
    assert rotated is not None
    assert rotated.chain_started_at == original.chain_started_at


def test_refresh_refuses_a_forged_or_invite_token(client: TestClient) -> None:
    forged = tokens.mint_session_token(
        signing_key="a-different-key",
        claims=tokens.SessionClaims(jti="x", patient_id=PATIENT_ID, tenant=TENANT),
        lifetime=tokens.TokenLifetime(issued_at=int(time.time()), ttl_seconds=3600),
    )
    invite = tokens.mint_invite_token(
        signing_key=SIGNING_KEY,
        claims=tokens.InviteClaims(jti="i", patient_id=PATIENT_ID, tenant=TENANT, purpose="intake"),
        lifetime=tokens.TokenLifetime(issued_at=int(time.time()), ttl_seconds=900),
    )

    assert _uniform_401(_refresh(client, forged))
    # Type confusion: an invitation must never be spendable as a session.
    assert _uniform_401(_refresh(client, invite))


# ---------------------------------------------------------------------------
# Access state + the kill switch
# ---------------------------------------------------------------------------


def test_access_state_reports_outstanding_invitation_then_live_session(
    client: TestClient, delivery: CapturingInviteDelivery, sms: FakeSmsGateway
) -> None:
    before = client.get(_access_url()).json()
    assert before == {
        "patient_id": PATIENT_ID,
        "invite_outstanding": False,
        "live_sessions": 0,
    }

    token, otp = _issue_and_capture(client, delivery, sms)
    invited = client.get(_access_url()).json()
    assert invited["invite_outstanding"] is True
    assert invited["live_sessions"] == 0

    _redeem(client, token, otp)
    active = client.get(_access_url()).json()
    # The invitation was burned by its single use; the session is live.
    assert active["invite_outstanding"] is False
    assert active["live_sessions"] == 1


def test_access_state_never_leaks_a_credential(
    client: TestClient, delivery: CapturingInviteDelivery, sms: FakeSmsGateway
) -> None:
    """The clinician can see THAT a patient has access, never the thing that
    grants it."""
    token, _otp = _issue_and_capture(client, delivery, sms)

    raw = client.get(_access_url()).text
    assert token not in raw


def test_revoke_kills_live_sessions_and_the_invitation_in_flight(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
) -> None:
    """Both halves. Revoking sessions while an unredeemed invitation is in
    flight would undo itself the moment the patient clicked the link."""
    token, otp = _issue_and_capture(client, delivery, sms)
    session_token = _redeem(client, token, otp).json()["session_token"]
    in_flight_token, in_flight_otp = _issue_and_capture(client, delivery, sms)

    response = client.delete(_access_url())

    assert response.status_code == 200
    assert response.json() == {
        "patient_id": PATIENT_ID,
        "sessions_revoked": 1,
        "invites_revoked": 1,
    }
    # The live session can no longer be rotated forward...
    assert _uniform_401(_refresh(client, session_token))
    # ...and the invitation that was still in the patient's inbox is dead.
    assert _uniform_401(_redeem(client, in_flight_token, in_flight_otp))
    assert client.get(_access_url()).json() == {
        "patient_id": PATIENT_ID,
        "invite_outstanding": False,
        "live_sessions": 0,
    }


def test_revoke_is_idempotent(client: TestClient, audit: _RecordingAudit) -> None:
    first = client.delete(_access_url())
    second = client.delete(_access_url())

    assert first.status_code == second.status_code == 200
    assert second.json()["sessions_revoked"] == 0
    assert second.json()["invites_revoked"] == 0
    assert audit.actions == [AuditAction.PATIENT_PORTAL_INVITE_REVOKED.value] * 2


def test_revoke_404s_for_unknown_patient(client: TestClient) -> None:
    assert client.delete(_access_url(UNKNOWN_PATIENT_ID)).status_code == 404


# ---------------------------------------------------------------------------
# The clinician routes are clinician-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("post", _invite_url()),
        ("get", _access_url()),
        ("delete", _access_url()),
    ],
)
def test_clinician_routes_refuse_an_unauthenticated_caller(
    stores: PortalStores,
    audit: _RecordingAudit,
    method: str,
    url: str,
) -> None:
    """No auth override this time — the real dependency runs and refuses.

    The patient-facing routes are deliberately NOT in this list: being
    reachable without a principal is their entire job.
    """
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)
    application.dependency_overrides[get_patient_repository] = _FakePatientRepository
    application.dependency_overrides[get_portal_stores] = lambda: stores
    application.dependency_overrides[get_audit_service] = lambda: audit

    response = getattr(TestClient(application), method)(url)
    assert response.status_code in {401, 403}


# ---------------------------------------------------------------------------
# Nothing a credential is made of reaches a log
# ---------------------------------------------------------------------------


def test_no_log_record_carries_a_credential_or_a_contact_detail(
    client: TestClient,
    delivery: CapturingInviteDelivery,
    sms: FakeSmsGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A whole cycle — invite, a wrong code, redeem, refresh, revoke — with
    every log record the routes emit checked against the five things that
    must never appear in one.

    The static scan in ``backend/scripts/check_phi_in_logs.py`` catches an
    identifier passed straight to a logger. It cannot catch one smuggled
    through an f-string or an exception message, and an exception message is
    exactly where a driver would quote the value it choked on — which here is
    a credential. So this runs the routes and reads what came out.
    """
    with caplog.at_level(logging.DEBUG):
        token, otp = _issue_and_capture(client, delivery, sms)
        wrong = "000000" if otp != "000000" else "111111"
        _redeem(client, token, wrong)
        session_token = _redeem(client, token, otp).json()["session_token"]
        rotated = _refresh(client, session_token).json()["session_token"]
        client.delete(_access_url())

    logged = "\n".join([record.getMessage() for record in caplog.records] + [str(caplog.text)])
    for secret in (
        token,
        otp,
        delivery.sent[-1].link,
        session_token,
        rotated,
        "patient@example.test",
        "+15005550006",
    ):
        assert secret not in logged


def test_audit_service_shape_matches_the_recording_double() -> None:
    """The fake audit above stands in for the real service; if its method
    names drift, these tests would keep passing against nothing."""
    assert hasattr(AuditService, "log")
    assert hasattr(AuditService, "log_patient_principal_action")
