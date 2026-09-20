# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP tests for signing out and for the capability document.

The real handlers on a fresh app, with the patient front door and the
session store swapped for in-memory doubles. What these are FOR:

* **Signing out of one device signs out one device.** A second session for
  the same patient keeps working — otherwise "sign out" on a library
  computer would knock the patient off their phone.
* **Signing out everywhere needs the second factor**, because it acts on
  devices that are not in the room.
* **The handle a sign-out acts on comes off the principal**, so there is no
  id in the request to point at somebody else.
* **Signing out does not withdraw access.** Invitations are the clinician's
  kill switch; a patient ending their own sessions is not the same event.
* **The capability document is the intersection**, and a single-factor
  caller may read it, because it says nothing about them.

The database-backed session store is proven separately against a freshly
provisioned practice schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_resolver_registry,
)
from app.auth.patient_context import (
    __name__ as patient_context_name,
)
from app.db import get_db_session
from app.models.audit import AuditAction
from app.portal.account_routes import router
from app.portal.practice_routes import PracticeAddress
from app.portal.store import InMemoryPortalSessionStore, PortalSessionRecord
from app.services.audit_service import get_audit_service
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

TENANT = "practice_abc123"
PRACTICE_NAME = "Meadowlark Counseling"
SIGNING_KEY = "account-route-test-key-not-a-real-secret"

PATIENT_A = "patient-a"
PATIENT_B = "patient-b"

# One token per (patient, session), so "this device" is a real distinction.
TOKEN_A1 = "token-a-phone"
TOKEN_A2 = "token-a-laptop"
TOKEN_A1_WEAK = "token-a-phone-single-factor"
TOKEN_B1 = "token-b-phone"

JTI_A1 = "session-a1"
JTI_A2 = "session-a2"
JTI_B1 = "session-b1"

NOW = 1_800_000_000

_PRINCIPALS: dict[str, tuple[str, str, AuthStrength]] = {
    TOKEN_A1: (PATIENT_A, JTI_A1, AuthStrength.STEPPED_UP),
    TOKEN_A2: (PATIENT_A, JTI_A2, AuthStrength.STEPPED_UP),
    TOKEN_A1_WEAK: (PATIENT_A, JTI_A1, AuthStrength.SINGLE_FACTOR),
    TOKEN_B1: (PATIENT_B, JTI_B1, AuthStrength.STEPPED_UP),
}


class _StubResolver:
    """A front door that hands back a principal carrying its session handle.

    The handle is the part under test: the real resolver reads it off the
    session ROW, and every route here acts on it rather than on anything in
    the request.
    """

    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        found = _PRINCIPALS.get(credential.value)
        if found is None:
            return None
        patient_id, jti, strength = found
        return PatientContext(
            patient_id=patient_id,
            practice_schema=TENANT,
            credential_kind="portal_session",
            auth_strength=strength,
            session_id=jti,
        )


class _RecordingAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.changes: list[dict[str, Any]] = []

    def log_patient_principal_action(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.actions.append(str(action))
        self.changes.append(kwargs.get("changes") or {})


def _record(jti: str, patient_id: str, *, revoked_at: int | None = None) -> PortalSessionRecord:
    return PortalSessionRecord(
        jti=jti,
        patient_id=patient_id,
        issued_at=NOW,
        expires_at=NOW + 3600,
        chain_started_at=NOW,
        revoked_at=revoked_at,
    )


@pytest.fixture(autouse=True)
def _portal_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("PORTAL_MODULES", "intake,messaging")
    yield
    get_settings.cache_clear()


@pytest.fixture
def sessions() -> InMemoryPortalSessionStore:
    store = InMemoryPortalSessionStore()
    store.record(_record(JTI_A1, PATIENT_A))
    store.record(_record(JTI_A2, PATIENT_A))
    store.record(_record(JTI_B1, PATIENT_B))
    return store


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


@pytest.fixture
def app(
    sessions: InMemoryPortalSessionStore,
    audit: _RecordingAudit,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)

    registry = PatientResolverRegistry()
    registry.register(_StubResolver())
    application.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    application.dependency_overrides[get_audit_service] = lambda: audit
    # The routes take the request's tenant-scoped session only to build the
    # store; the store itself is the double, so the session never has to be
    # real.
    application.dependency_overrides[get_db_session] = object

    # ``get_patient_context`` arms search_path and the patient GUC on a real
    # session. There is no database here, so the arming is stubbed out —
    # exactly as the patient chat route tests do it.
    import sys  # noqa: PLC0415

    module = sys.modules[patient_context_name]
    monkeypatch.setattr(module, "get_db_session", object)
    monkeypatch.setattr(module, "set_tenant_schema", lambda _s, _schema: None)
    monkeypatch.setattr(module, "arm_current_patient_id", lambda _s, _p: None)

    # The stores the handlers build from the request session.
    from app.portal import account_routes  # noqa: PLC0415

    monkeypatch.setattr(account_routes, "DbPortalSessionStore", lambda _session: sessions)
    # The header name comes out of the platform directory, which is a
    # standalone session these tests have no database for. The capability
    # document's load-bearing half is the module map; this is decoration,
    # and it is pinned to a constant so a change to it fails visibly.
    monkeypatch.setattr(
        account_routes,
        "practice_address_for_schema",
        lambda _schema: PracticeAddress(slug="example", display_name=PRACTICE_NAME, enabled=True),
    )
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestSigningOutOneDevice:
    def test_it_revokes_this_session(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        response = client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))

        assert response.status_code == 200
        assert response.json() == {"sessions_revoked": 1}
        record = sessions.get(JTI_A1)
        assert record is not None
        assert record.revoked_at is not None

    def test_the_patients_other_session_still_authenticates(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        """The whole point of "this device" rather than "all devices".

        Someone signing out of a library computer must not be knocked off
        their phone.
        """
        client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))

        other = sessions.get(JTI_A2)
        assert other is not None
        assert other.revoked_at is None
        assert sessions.live_count_for_patient(PATIENT_A, now=NOW) == 1

    def test_another_patients_session_is_untouched(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))

        theirs = sessions.get(JTI_B1)
        assert theirs is not None
        assert theirs.revoked_at is None

    def test_a_single_factor_principal_may_still_sign_out(self, client: TestClient) -> None:
        """No step-up bar, deliberately.

        Refusing to let somebody leave because they have not proved enough
        is backwards, and a principal that wanted to cause harm would not
        choose to end its own session.
        """
        response = client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1_WEAK))

        assert response.status_code == 200

    def test_signing_out_twice_succeeds_and_counts_zero(self, client: TestClient) -> None:
        """Idempotent, and honest about it rather than answering an error."""
        client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))
        second = client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))

        assert second.status_code == 200
        assert second.json() == {"sessions_revoked": 0}

    def test_no_credential_is_401(self, client: TestClient) -> None:
        assert client.post("/api/patient/auth/logout").status_code == 401

    def test_it_audits_the_revocation_with_the_session_handle(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))

        assert AuditAction.PATIENT_SESSION_REVOKED.value in " ".join(audit.actions)
        assert audit.changes[-1] == {"scope": "session", "sessions_revoked": 1}


class TestSigningOutEverywhere:
    def test_it_revokes_every_session_for_this_patient(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        response = client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A1))

        assert response.status_code == 200
        assert response.json() == {"sessions_revoked": 2}
        assert sessions.live_count_for_patient(PATIENT_A, now=NOW) == 0

    def test_it_leaves_another_patients_sessions_alone(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A1))

        assert sessions.live_count_for_patient(PATIENT_B, now=NOW) == 1

    def test_a_single_factor_principal_is_refused(self, client: TestClient) -> None:
        """The one sign-out that needs the second factor.

        It acts on devices that are not in the room, so a link that reached
        the wrong inbox must not be able to lock the real patient out.
        """
        response = client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A1_WEAK))

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "STEP_UP_REQUIRED"

    def test_a_refused_call_revokes_nothing(
        self, client: TestClient, sessions: InMemoryPortalSessionStore
    ) -> None:
        client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A1_WEAK))

        assert sessions.live_count_for_patient(PATIENT_A, now=NOW) == 2

    def test_it_audits_the_scope_and_the_count(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A1))

        assert audit.changes[-1] == {"scope": "all", "sessions_revoked": 2}


class TestSigningOutIsNotWithdrawal:
    def test_the_challenge_store_is_never_touched(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Burning invitations is the clinician's kill switch, not the patient's.

        A patient who signs out everywhere and then opens the invitation
        still sitting in their inbox should be let back in. If a sign-out
        ever reached for the challenge store, this is where it shows up: the
        handlers are built with no challenge store at all, so touching one
        raises rather than quietly consuming an invitation.
        """
        from app.portal import account_routes  # noqa: PLC0415

        built: list[Any] = []
        real_builder = account_routes.build_portal_auth_service

        def _spy(**kwargs: Any) -> Any:
            built.append(kwargs.get("store"))
            return real_builder(**kwargs)

        monkeypatch.setattr(account_routes, "build_portal_auth_service", _spy)

        client.post("/api/patient/auth/logout", headers=_auth(TOKEN_A1))
        client.post("/api/patient/auth/logout-all", headers=_auth(TOKEN_A2))

        assert built == [None, None]


class TestTheCapabilityDocument:
    def test_it_reports_the_intersection_of_configured_and_mounted(
        self, client: TestClient
    ) -> None:
        """Configured is intake+messaging; this app mounts neither module.

        So both come back off — which is the intersection doing its job. The
        assembled application's answer is covered in
        ``test_portal_modules.py``; what matters here is that the route
        reads the MOUNTED half off its own app rather than echoing the
        setting back.
        """
        body = client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1)).json()

        assert body["modules"] == {
            "intake": False,
            "messaging": False,
            "documents": False,
            "appointments": False,
            "billing": False,
            "chat": False,
        }

    def test_a_single_factor_caller_may_read_it(self, client: TestClient) -> None:
        """It says nothing about the person asking.

        Which modules exist is the same answer for every patient of the
        practice, and a shell that could not draw its own navigation until
        the second factor cleared would show an empty frame mid-sign-in.
        """
        response = client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1_WEAK))

        assert response.status_code == 200
        assert response.json()["auth_strength"] == "single_factor"

    def test_it_reports_the_callers_auth_strength(self, client: TestClient) -> None:
        body = client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1)).json()

        assert body["auth_strength"] == "stepped_up"

    def test_it_names_the_practice_for_the_header(self, client: TestClient) -> None:
        """So the header after sign-in says what the header before it said.

        Both come from the same platform directory, keyed on the practice
        rather than copied between surfaces.
        """
        body = client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1)).json()

        assert body["practice"] == {"display_name": PRACTICE_NAME}

    def test_no_credential_is_401(self, client: TestClient) -> None:
        assert client.get("/api/patient/capabilities").status_code == 401

    def test_two_patients_get_the_same_document(self, client: TestClient) -> None:
        """Nothing in it is about the caller, so nothing in it may differ."""
        a = client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1)).json()
        b = client.get("/api/patient/capabilities", headers=_auth(TOKEN_B1)).json()

        assert a == b

    def test_reading_it_writes_no_audit_row(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        """Deployment shape is not a disclosure.

        A row per portal visit would bury the ones that carry forensic
        weight — the same argument the patient's own message reads make.
        """
        client.get("/api/patient/capabilities", headers=_auth(TOKEN_A1))

        assert audit.actions == []
