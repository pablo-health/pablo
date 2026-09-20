# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient portal session cannot cross practices.

Every isolation test shipped with the secure-messaging store and the intake
API is same-practice: patient A versus patient B inside one schema, proven
by the row policy. That leaves an ENGINE property unproven — that a session
issued for practice X can never read or write practice Y's portal data, even
when it names Y's row ids by hand. Cross-practice isolation here is not a
row policy at all: ``patient_message_threads``, ``patient_messages`` and
``patient_intake_submissions`` carry no ``practice_id`` column, because the
schema location IS the tenant scope (see each row's own docstring in
``app/db/models.py``). The only thing standing between two practices is that
``get_patient_context`` enters the schema the resolver names and nothing
else, and that a clinician's own tenant context is what the middleware
resolved from their own identity, not from anything the client sent.

So this module runs the real stack, twice: two schemas built by the real
``create_practice_schema``, a real patient row in each, and a REAL portal
session for each patient minted through the engine's own credential
lifecycle — invite, then redemption over HTTP — using the capturing email
double and the in-memory SMS gateway from ``app.portal.delivery`` rather
than a stub resolver. The only hand-built tokens in this file are the forged
ones in ``TestForgedAndOrphanedTokens``, which is the point of that class.

Cases, mirroring the design doc:

1. Control — each patient reaches their own thread.
2. A patient naming the other practice's thread id gets 404 on every verb,
   and nothing is written to the schema that refused them.
3. A patient's thread list never contains the other practice's thread ids,
   even after that practice creates several more.
4. An intake submission and the intake form both stay inside the submitting
   patient's own schema.
5. Tokens: an altered claim without a re-sign, a well-signed claim naming a
   schema that was never provisioned, and a revoked session — all one 401,
   never a 500, and the ghost schema is never created.
6. A clinician's own tenant context (practice X) never reaches practice Y's
   threads or patient, whether by thread id or by patient id.
7. None of the refused calls above writes an audit row anywhere, and no
   message body or patient name reaches a log line.

**Two documented findings, not a patched engine.** One sub-case of 6 does
not match the design as written — see
``TestClinicianTenantContextNeverReachesAnotherPractice`` for what actually
happens and why it is a status-code gap rather than a disclosure. The same
route also fails a sub-case of 7: it audits an empty result unconditionally,
so a lookup that finds nothing still writes a row in the caller's OWN
schema naming the other practice's patient id — see
``TestRefusedCrossPracticeCallsAuditNothing`` for the exact shape. Per this
module's brief, both fixes belong in a follow-up change to the engine, not
here.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA_Y = f"practice_test_xpr_y_{_SUFFIX}"
_CLINICIAN_X_ID = str(uuid.uuid4())
_CLINICIAN_X_EMAIL = f"clinician-x-{_SUFFIX}@example.test"
_CLINICIAN_TOKEN = f"clinician-x-sentinel-{_SUFFIX}"

_PATIENT_X_EMAIL = "patient-x@example.test"
_PATIENT_X_PHONE = "+15005550010"
_PATIENT_Y_EMAIL = "patient-y@example.test"
_PATIENT_Y_PHONE = "+15005550020"
_PATIENT_X_FIRST = "Xavier"
_PATIENT_Y_FIRST = "Yolanda"

_SIGNING_KEY = "cross-practice-isolation-signing-key-not-a-real-secret"
_PORTAL_ORIGIN = "https://portal.example.test"

THREADS_URL = "/api/patient/messages/threads"
INTAKE_FORM_URL = "/api/patient/intake/form"
INTAKE_SUBMIT_URL = "/api/patient/intake/submissions"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _intake_body() -> dict[str, object]:
    return {
        "name_confirmed": True,
        "dob_confirmed": True,
        "corrections": None,
        "reason_text": "Feeling anxious about a change at work.",
        "phq9": dict.fromkeys((str(i) for i in range(1, 10)), 0),
        "gad7": dict.fromkeys((str(i) for i in range(1, 8)), 0),
    }


def _retarget_claim_without_resigning(token: str, claim: str, new_value: str) -> str:
    """Change one JWT claim and keep the ORIGINAL signature.

    This is the forgery case 5 asks for: a claim altered without a re-sign,
    as opposed to a token corrupted at random. Decoding and re-encoding the
    payload segment on its own changes nothing the signature covers in a way
    that matters here — the point is that the signature was computed over
    the OLD payload and is presented against a NEW one, so verification must
    fail regardless of which byte moved.
    """
    header_b64, payload_b64, signature_b64 = token.split(".")
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    payload[claim] = new_value
    new_payload = json.dumps(payload).encode()
    new_payload_b64 = base64.urlsafe_b64encode(new_payload).rstrip(b"=").decode()
    return f"{header_b64}.{new_payload_b64}.{signature_b64}"


def _audit_count(engine: Engine, schema: str) -> int:
    """Every audit row in *schema*, regardless of which actor wrote it.

    ``audit_logs`` is actor-scoped (a clinician reads only their own rows,
    a patient reads none at all — see ``app/db/__init__.py``), so a plain
    unarmed SELECT would silently see zero rows whether or not any exist.
    Arming the retention-purge GUC is the one documented seam that reads
    every row regardless of actor, and it is read-only here — this helper
    never sets ``allow_audit_purge`` anywhere near a DELETE.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        return conn.execute(text("SELECT count(*) FROM audit_logs")).scalar_one()


def _submission_count(engine: Engine, schema: str, patient_id: str) -> int:
    """*patient_id*'s own intake submissions in *schema*.

    ``patient_intake_submissions`` is patient-scoped RLS (self-read only),
    so this arms that patient's own GUC — an unarmed connection would see
    zero rows whether or not any exist, which would make an "unchanged
    count" assertion vacuous.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient_id}
        )
        return conn.execute(text("SELECT count(*) FROM patient_intake_submissions")).scalar_one()


def _message_count(engine: Engine, schema: str, thread_id: str, patient_id: str) -> int:
    """Messages in one thread, read as the thread's own owning patient.

    ``patient_messages`` is patient-scoped RLS too — see ``_submission_count``.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient_id}
        )
        return conn.execute(
            text("SELECT count(*) FROM patient_messages WHERE thread_id = CAST(:t AS uuid)"),
            {"t": thread_id},
        ).scalar_one()


def _schema_exists(engine: Engine, schema: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": schema},
        ).first()
    return row is not None


class _MintedSession:
    """One patient's real, engine-minted session, plus the row that backs it."""

    def __init__(self, session_token: str, jti: str) -> None:
        self.session_token = session_token
        self.jti = jti


def _issue_and_redeem(
    client: TestClient, *, schema: str, patient_id: str, phone: str, email: str
) -> _MintedSession:
    """Invite, then redeem over HTTP — the engine's own lifecycle, twice.

    ``CapturingInviteDelivery`` and ``FakeSmsGateway`` stand in for the two
    delivery channels (see ``app/portal/delivery.py``); everything else —
    the service, the stores, the signature, the redeem route — is real. The
    resolver that later answers for this session is the one the app
    registered at startup, not an override.
    """
    from app.db import create_standalone_session  # noqa: PLC0415
    from app.portal.db_store import DbPortalAuthStore, DbPortalSessionStore  # noqa: PLC0415
    from app.portal.delivery import CapturingInviteDelivery, FakeSmsGateway  # noqa: PLC0415
    from app.portal.factory import build_portal_auth_service  # noqa: PLC0415
    from app.portal.tokens import verify_session_token  # noqa: PLC0415

    delivery = CapturingInviteDelivery()
    sms = FakeSmsGateway()

    session = create_standalone_session(schema)
    try:
        service = build_portal_auth_service(
            store=DbPortalAuthStore(session, tenant=schema),
            sessions=DbPortalSessionStore(session),
            sms=sms,
        )
        issued = service.issue_invite(patient_id=patient_id, tenant=schema, phone=phone)
        delivery.send_invite(to_email=email, link=f"{_PORTAL_ORIGIN}/portal#invite={issued.token}")
        session.commit()
    finally:
        session.close()

    assert delivery.sent, "the invitation was never emailed"
    link = delivery.sent[-1].link
    assert link.startswith(f"{_PORTAL_ORIGIN}/portal#invite=")
    token = link.split("#invite=", 1)[1]

    assert sms.sent, "the step-up code was never texted"
    digits = "".join(c for c in sms.sent[-1].body if c.isdigit())
    otp = digits[:6]

    redeemed = client.post("/api/patient/auth/redeem", json={"token": token, "otp": otp})
    assert redeemed.status_code == 200, redeemed.text
    session_token: str = redeemed.json()["session_token"]

    claims = verify_session_token(signing_key=_SIGNING_KEY, token=session_token)
    return _MintedSession(session_token=session_token, jti=claims.jti)


# ---------------------------------------------------------------------------
# Module-scoped scaffolding: two real practices, two real patients, real
# sessions, and one clinician wired to practice X only.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _app_imported() -> None:
    """Import the app before ``caplog`` or any settings cache is touched.

    ``app.main`` configures logging and reads settings at import; doing that
    from inside a test would empty a capture the same test is about to read.
    """
    import app.main  # noqa: PLC0415, F401


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module", autouse=True)
def _portal_settings() -> Iterator[None]:
    """The signing key and origin every mint/verify call in this module needs.

    Module-scoped and set once: nothing here needs a different key mid-file,
    and every fixture below that mints or verifies a token reads it from
    settings at call time.
    """
    from app.settings import get_settings  # noqa: PLC0415

    mp = pytest.MonkeyPatch()
    mp.setenv("PORTAL_TOKEN_SIGNING_KEY", _SIGNING_KEY)
    mp.setenv("PORTAL_WEB_BASE_URL", _PORTAL_ORIGIN)
    get_settings.cache_clear()
    yield
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def practice_x(engine: Engine) -> Iterator[str]:
    """Practice X, wired for real clinician resolution.

    ``_seeded_practice`` inserts the platform rows
    ``_resolve_practice_from_email`` actually reads (a practice, a platform
    user, an email mapping) and provisions the schema through
    ``create_practice_schema`` — the same call the pentest and patients-API
    integration suites exercise. Reused rather than re-implemented so this
    module covers the real lookup, not a stand-in for it.
    """
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    with _seeded_practice(engine, _CLINICIAN_X_EMAIL) as schema:
        yield schema


@pytest.fixture(scope="module")
def practice_y(engine: Engine) -> Iterator[str]:
    """Practice Y. No clinician ever legitimately reaches it in this module,
    so it gets no email mapping — only the schema and a patient."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        # Warm the pool: policy CREATEs inside create_practice_schema call
        # has_patient_access() unqualified, which resolves only when
        # "practice" is on this connection's search_path.
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA_Y)
    yield _SCHEMA_Y
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA_Y}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_records(engine: Engine, practice_x: str, practice_y: str) -> tuple[str, str]:
    """One patient in each schema, with the contact info a real invite needs.

    Patient X also gets a ``patient_clinicians`` grant to clinician X — the
    control case 6 needs before it can prove clinician X reaches nothing of
    Y's.
    """
    patient_x = str(uuid.uuid4())
    patient_y = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {practice_x}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN_X_ID},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, email, phone, "
                "created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), :first, 'Tester', lower(:first), 'tester', "
                "'active', 0, :e, :ph, now(), now())"
            ),
            {
                "p": patient_x,
                "first": _PATIENT_X_FIRST,
                "e": _PATIENT_X_EMAIL,
                "ph": _PATIENT_X_PHONE,
            },
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:p AS uuid), :u, :u)"
            ),
            {"p": patient_x, "u": _CLINICIAN_X_ID},
        )

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {practice_y}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": str(uuid.uuid4())},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, email, phone, "
                "created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), :first, 'Tester', lower(:first), 'tester', "
                "'active', 0, :e, :ph, now(), now())"
            ),
            {
                "p": patient_y,
                "first": _PATIENT_Y_FIRST,
                "e": _PATIENT_Y_EMAIL,
                "ph": _PATIENT_Y_PHONE,
            },
        )

    return patient_x, patient_y


@pytest.fixture(scope="module")
def clinician_x_user():  # type: ignore[no-untyped-def]
    from app.models import User  # noqa: PLC0415

    return User(
        id=_CLINICIAN_X_ID,
        email=_CLINICIAN_X_EMAIL,
        name="Clinician X",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture(scope="module", autouse=True)
def _wire_clinician_x(practice_x: str, clinician_x_user) -> Iterator[None]:  # type: ignore[no-untyped-def]
    """Wire ONE real clinician into practice X, and nothing else.

    The middleware verifies a clinician credential and resolves a schema
    from its email BEFORE any dependency runs (``DatabaseSessionMiddleware``
    docstring) — that lookup is not something ``get_tenant_context``
    overrides can reach, so the identity stash has to be real too, gated on
    a sentinel token rather than replaced unconditionally. An unconditional
    stash (the shape ``test_patients_api_e2e.py`` uses for a clinician-only
    suite) would hand every PATIENT bearer token a clinician identity too,
    since ``DatabaseSessionMiddleware`` runs it for every request — and that
    would make ``get_patient_context`` refuse every patient call in this
    module as "a clinician credential presented to a patient route".

    Dependency overrides are FastAPI-level and only ever consulted by a
    route that actually depends on the overridden callable — the patient
    routes below depend on none of them — so wiring them here does nothing
    to the patient-session tests in this module.
    """
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db.middleware import _verify_and_stash_clinician_identity  # noqa: F401, PLC0415
    from app.main import app  # noqa: PLC0415

    mp = pytest.MonkeyPatch()

    def _stash_only_the_sentinel(request) -> None:  # type: ignore[no-untyped-def]
        from app.auth.providers import VerifiedIdentity  # noqa: PLC0415

        auth_header = request.headers.get("authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if not scheme or token.strip() != _CLINICIAN_TOKEN:
            return
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=_CLINICIAN_X_ID,
            email=_CLINICIAN_X_EMAIL,
            mfa_satisfied=True,
            claims={},
        )

    mp.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_only_the_sentinel)

    def _arm_and_return_id() -> str:
        # Production arms this GUC at one shared seam inside the real
        # ``_resolve_user`` (``app/auth/service.py``, "arm at this single
        # shared seam covers every authenticated HTTP request") precisely so
        # routes that never depend on ``get_tenant_context`` — both
        # message-thread clinician routes below are exactly that — still run
        # under a clinician-scoped RLS principal. Overriding every identity
        # dependency wholesale skips that seam entirely, so it has to be
        # redone here or `has_patient_access()` (and the audit-log insert
        # policy) see no principal at all and deny everything, including
        # clinician X's own data.
        from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415

        arm_current_user_id(get_db_session(), _CLINICIAN_X_ID)
        return _CLINICIAN_X_ID

    def _arm_and_return_user():  # type: ignore[no-untyped-def]
        _arm_and_return_id()
        return clinician_x_user

    def _tenant_context() -> TenantContext:
        _arm_and_return_id()
        return TenantContext(
            user_id=_CLINICIAN_X_ID,
            practice_id="cross-practice-isolation-x",
            practice_schema=practice_x,
        )

    app.dependency_overrides[get_current_user_id] = _arm_and_return_id
    app.dependency_overrides[get_current_user] = _arm_and_return_user
    app.dependency_overrides[get_current_user_no_mfa] = _arm_and_return_user
    app.dependency_overrides[require_active_subscription] = _arm_and_return_user
    app.dependency_overrides[require_baa_acceptance] = _arm_and_return_user
    app.dependency_overrides[get_tenant_context] = _tenant_context

    try:
        yield
    finally:
        app.dependency_overrides.clear()
        mp.undo()


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    from app.main import app  # noqa: PLC0415
    from app.rate_limit import reset_portal_limiters  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    reset_portal_limiters()
    yield TestClient(app)
    reset_portal_limiters()


@pytest.fixture(scope="module")
def patient_sessions(
    client: TestClient, practice_x: str, practice_y: str, patient_records: tuple[str, str]
) -> tuple[_MintedSession, _MintedSession]:
    """A real, redeemed session for patient X and one for patient Y."""
    patient_x, patient_y = patient_records
    session_x = _issue_and_redeem(
        client,
        schema=practice_x,
        patient_id=patient_x,
        phone=_PATIENT_X_PHONE,
        email=_PATIENT_X_EMAIL,
    )
    session_y = _issue_and_redeem(
        client,
        schema=practice_y,
        patient_id=patient_y,
        phone=_PATIENT_Y_PHONE,
        email=_PATIENT_Y_EMAIL,
    )
    return session_x, session_y


@pytest.fixture(scope="module")
def threads(
    client: TestClient, patient_sessions: tuple[_MintedSession, _MintedSession]
) -> tuple[str, str]:
    """One thread each, started by the owning patient through the real route."""
    session_x, session_y = patient_sessions

    resp_x = client.post(
        THREADS_URL,
        json={"subject": "Billing question", "body": "Quick question about my invoice."},
        headers=_headers(session_x.session_token),
    )
    assert resp_x.status_code == 201, resp_x.text

    resp_y = client.post(
        THREADS_URL,
        json={"subject": "Scheduling", "body": "Could we move Thursday's session?"},
        headers=_headers(session_y.session_token),
    )
    assert resp_y.status_code == 201, resp_y.text

    return resp_x.json()["id"], resp_y.json()["id"]


# ---------------------------------------------------------------------------
# Case 1 — control
# ---------------------------------------------------------------------------


class TestControlEachPatientReachesOnlyTheirOwnThread:
    """Non-vacuity for every negative below: the route works at all."""

    def test_patient_x_lists_and_opens_their_own_thread(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
    ) -> None:
        session_x, _ = patient_sessions
        thread_x, thread_y = threads

        listed = client.get(THREADS_URL, headers=_headers(session_x.session_token))
        assert listed.status_code == 200
        ids = [t["id"] for t in listed.json()["data"]]
        assert thread_x in ids
        assert thread_y not in ids

        opened = client.get(f"{THREADS_URL}/{thread_x}", headers=_headers(session_x.session_token))
        assert opened.status_code == 200
        assert opened.json()["id"] == thread_x

    def test_patient_y_lists_and_opens_their_own_thread(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
    ) -> None:
        _, session_y = patient_sessions
        thread_x, thread_y = threads

        listed = client.get(THREADS_URL, headers=_headers(session_y.session_token))
        assert listed.status_code == 200
        ids = [t["id"] for t in listed.json()["data"]]
        assert thread_y in ids
        assert thread_x not in ids

        opened = client.get(f"{THREADS_URL}/{thread_y}", headers=_headers(session_y.session_token))
        assert opened.status_code == 200
        assert opened.json()["id"] == thread_y


# ---------------------------------------------------------------------------
# Case 2 — naming the other practice's thread outright
# ---------------------------------------------------------------------------


class TestPatientCannotReachAnotherPracticesThread:
    def test_getting_the_other_practices_thread_by_id_is_404(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
    ) -> None:
        session_x, _ = patient_sessions
        thread_x, thread_y = threads

        # Control: X's own thread opens fine on this exact route shape.
        own = client.get(f"{THREADS_URL}/{thread_x}", headers=_headers(session_x.session_token))
        assert own.status_code == 200

        other = client.get(f"{THREADS_URL}/{thread_y}", headers=_headers(session_x.session_token))
        assert other.status_code == 404

    def test_posting_into_the_other_practices_thread_is_404_and_writes_nothing(  # noqa: PLR0913
        self,
        engine: Engine,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
        patient_records: tuple[str, str],
        practice_y: str,
    ) -> None:
        session_x, _ = patient_sessions
        _, thread_y = threads
        patient_x, patient_y = patient_records

        # Checked from two angles: as Y, the thread's rightful owner (the
        # natural reading of "Y's row count"), and as X, the actor whose
        # principal a leaked row would actually carry — the route inserts
        # with the CALLER's own patient_id, never the thread's, so a leaked
        # row here would be attributed to X, not to Y.
        before_as_y = _message_count(engine, practice_y, thread_y, patient_y)
        before_as_x = _message_count(engine, practice_y, thread_y, patient_x)

        resp = client.post(
            f"{THREADS_URL}/{thread_y}/messages",
            json={"body": "let me into your thread"},
            headers=_headers(session_x.session_token),
        )
        assert resp.status_code == 404

        after_as_y = _message_count(engine, practice_y, thread_y, patient_y)
        after_as_x = _message_count(engine, practice_y, thread_y, patient_x)
        assert after_as_y == before_as_y, "a refused send changed what Y sees in their own thread"
        assert after_as_x == before_as_x, "a refused send left a row attributed to X in Y's schema"

    def test_marking_the_other_practices_thread_read_is_404(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
    ) -> None:
        session_x, _ = patient_sessions
        _, thread_y = threads

        resp = client.post(
            f"{THREADS_URL}/{thread_y}/read", headers=_headers(session_x.session_token)
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Case 3 — listing never leaks the other practice's ids
# ---------------------------------------------------------------------------


class TestThreadListingNeverCrossesPractices:
    def test_xs_thread_list_never_contains_ys_threads_even_after_y_makes_more(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
    ) -> None:
        session_x, session_y = patient_sessions
        thread_x, thread_y = threads

        extra_y_ids = []
        for i in range(3):
            resp = client.post(
                THREADS_URL,
                json={"body": f"Another Y thread, number {i}"},
                headers=_headers(session_y.session_token),
            )
            assert resp.status_code == 201, resp.text
            extra_y_ids.append(resp.json()["id"])

        listed = client.get(THREADS_URL, headers=_headers(session_x.session_token))
        assert listed.status_code == 200
        ids_x = {t["id"] for t in listed.json()["data"]}

        assert thread_x in ids_x
        assert thread_y not in ids_x
        assert ids_x.isdisjoint(extra_y_ids), "patient X's list picked up one of Y's new threads"


# ---------------------------------------------------------------------------
# Case 4 — intake stays inside the submitting patient's own schema
# ---------------------------------------------------------------------------


class TestIntakeStaysInItsOwnSchema:
    def test_the_form_carries_the_callers_own_identity_never_the_others(
        self, client: TestClient, patient_sessions: tuple[_MintedSession, _MintedSession]
    ) -> None:
        session_x, session_y = patient_sessions

        form_x = client.get(INTAKE_FORM_URL, headers=_headers(session_x.session_token))
        assert form_x.status_code == 200
        assert form_x.json()["identity"]["first_name"] == _PATIENT_X_FIRST

        form_y = client.get(INTAKE_FORM_URL, headers=_headers(session_y.session_token))
        assert form_y.status_code == 200
        assert form_y.json()["identity"]["first_name"] == _PATIENT_Y_FIRST

    def test_a_submission_lands_only_in_the_submitting_patients_schema(  # noqa: PLR0913
        self,
        engine: Engine,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        patient_records: tuple[str, str],
        practice_x: str,
        practice_y: str,
    ) -> None:
        session_x, _ = patient_sessions
        patient_x, _ = patient_records

        before_x = _submission_count(engine, practice_x, patient_x)
        # Armed as patient X, but pointed at schema Y: this is the actual
        # leak shape a schema-resolution bug would produce (the row still
        # carries patient X's id — the app never learns a patient id from
        # the client — it would just be sitting in the wrong schema).
        before_y = _submission_count(engine, practice_y, patient_x)

        resp = client.post(
            INTAKE_SUBMIT_URL, json=_intake_body(), headers=_headers(session_x.session_token)
        )
        assert resp.status_code == 201, resp.text

        assert _submission_count(engine, practice_x, patient_x) == before_x + 1
        assert _submission_count(engine, practice_y, patient_x) == before_y


# ---------------------------------------------------------------------------
# Case 5 — forged and orphaned tokens
# ---------------------------------------------------------------------------


class TestForgedAndOrphanedTokens:
    def test_altering_the_practice_claim_without_resigning_is_401(
        self,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        practice_y: str,
    ) -> None:
        session_x, _ = patient_sessions

        # Control: the unmodified token still works.
        control = client.get(THREADS_URL, headers=_headers(session_x.session_token))
        assert control.status_code == 200

        forged = _retarget_claim_without_resigning(session_x.session_token, "tid", practice_y)
        resp = client.get(THREADS_URL, headers=_headers(forged))
        assert resp.status_code == 401

    def test_a_signed_token_naming_an_unprovisioned_practice_is_401_with_no_schema_created(
        self, engine: Engine, client: TestClient
    ) -> None:
        from app.portal.tokens import (  # noqa: PLC0415
            SessionClaims,
            TokenLifetime,
            mint_session_token,
        )

        ghost_schema = f"practice_ghost_{uuid.uuid4().hex[:8]}"
        assert not _schema_exists(engine, ghost_schema)

        token = mint_session_token(
            signing_key=_SIGNING_KEY,
            claims=SessionClaims(
                jti=uuid.uuid4().hex, patient_id=str(uuid.uuid4()), tenant=ghost_schema
            ),
            lifetime=TokenLifetime(issued_at=int(time.time()), ttl_seconds=3600),
        )

        resp = client.get(THREADS_URL, headers=_headers(token))
        assert resp.status_code == 401
        assert resp.status_code != 500

        assert not _schema_exists(engine, ghost_schema), (
            "presenting a token for an unprovisioned schema created it"
        )

    def test_a_revoked_session_is_401_on_every_patient_route(
        self,
        client: TestClient,
        practice_y: str,
        patient_records: tuple[str, str],
    ) -> None:
        """A throwaway session, so revoking it here does not disturb the
        module-scoped session other tests in this file still rely on."""
        from app.db import create_standalone_session  # noqa: PLC0415
        from app.portal.db_store import DbPortalSessionStore  # noqa: PLC0415

        _, patient_y = patient_records
        throwaway = _issue_and_redeem(
            client,
            schema=practice_y,
            patient_id=patient_y,
            phone=_PATIENT_Y_PHONE,
            email=_PATIENT_Y_EMAIL,
        )

        # Control: it works before revocation.
        control = client.get(THREADS_URL, headers=_headers(throwaway.session_token))
        assert control.status_code == 200

        session = create_standalone_session(practice_y)
        try:
            DbPortalSessionStore(session).revoke(throwaway.jti, at=int(time.time()))
            session.commit()
        finally:
            session.close()

        threads_resp = client.get(THREADS_URL, headers=_headers(throwaway.session_token))
        assert threads_resp.status_code == 401
        intake_resp = client.get(INTAKE_FORM_URL, headers=_headers(throwaway.session_token))
        assert intake_resp.status_code == 401


# ---------------------------------------------------------------------------
# Case 6 — a clinician's own tenant context never reaches the other practice
# ---------------------------------------------------------------------------


class TestClinicianTenantContextNeverReachesAnotherPractice:
    """Clinician X's tenant context is practice X, resolved from their own
    identity — never from anything a client sends. Naming Y's ids must not
    move that context."""

    def test_control_clinician_x_can_open_their_own_patients_thread(
        self, client: TestClient, threads: tuple[str, str]
    ) -> None:
        thread_x, _ = threads
        resp = client.get(f"/api/message-threads/{thread_x}", headers=_headers(_CLINICIAN_TOKEN))
        assert resp.status_code == 200
        assert resp.json()["id"] == thread_x

    def test_clinician_x_opening_ys_thread_by_id_is_404(
        self, client: TestClient, threads: tuple[str, str]
    ) -> None:
        _, thread_y = threads
        resp = client.get(f"/api/message-threads/{thread_y}", headers=_headers(_CLINICIAN_TOKEN))
        assert resp.status_code == 404

    def test_clinician_x_listing_ys_patient_threads_discloses_nothing(
        self, client: TestClient, patient_records: tuple[str, str]
    ) -> None:
        """The property that has to hold regardless of status code: no data
        about Y's threads crosses over. See the 404 test below for the
        status code the design asks for."""
        _, patient_y = patient_records
        resp = client.get(
            f"/api/patients/{patient_y}/message-threads", headers=_headers(_CLINICIAN_TOKEN)
        )
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert body["data"] == []
            assert body["total"] == 0

    def test_clinician_x_listing_ys_patient_threads_is_404(
        self, client: TestClient, patient_records: tuple[str, str]
    ) -> None:
        _, patient_y = patient_records
        resp = client.get(
            f"/api/patients/{patient_y}/message-threads", headers=_headers(_CLINICIAN_TOKEN)
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Case 7 — the refusals above audit nothing, anywhere
# ---------------------------------------------------------------------------


class TestRefusedCrossPracticeCallsAuditNothing:
    def test_no_audit_rows_land_anywhere_and_no_body_or_name_reaches_a_log(  # noqa: PLR0913
        self,
        engine: Engine,
        client: TestClient,
        patient_sessions: tuple[_MintedSession, _MintedSession],
        threads: tuple[str, str],
        practice_x: str,
        practice_y: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session_x, _ = patient_sessions
        _, thread_y = threads
        thread_y_url = f"{THREADS_URL}/{thread_y}"
        x_headers = _headers(session_x.session_token)

        before_x = _audit_count(engine, practice_x)
        before_y = _audit_count(engine, practice_y)

        forbidden_body = "this body must never reach a log or an audit row"

        with caplog.at_level("WARNING"):
            get_thread = client.get(thread_y_url, headers=x_headers)
            assert get_thread.status_code == 404

            send_message = client.post(
                f"{thread_y_url}/messages", json={"body": forbidden_body}, headers=x_headers
            )
            assert send_message.status_code == 404

            mark_read = client.post(f"{thread_y_url}/read", headers=x_headers)
            assert mark_read.status_code == 404

            get_thread_as_clinician = client.get(
                f"/api/message-threads/{thread_y}", headers=_headers(_CLINICIAN_TOKEN)
            )
            assert get_thread_as_clinician.status_code == 404

        after_x = _audit_count(engine, practice_x)
        after_y = _audit_count(engine, practice_y)
        assert after_x == before_x, "a refused call wrote an audit row in the caller's own schema"
        assert after_y == before_y, "a refused call wrote a row in the schema it was refused from"

        log_text = caplog.text
        assert forbidden_body not in log_text
        assert _PATIENT_X_FIRST not in log_text
        assert _PATIENT_Y_FIRST not in log_text

    def test_the_listing_call_that_finds_nothing_still_writes_no_audit_row(
        self,
        engine: Engine,
        client: TestClient,
        patient_records: tuple[str, str],
        practice_x: str,
    ) -> None:
        _, patient_y = patient_records
        before_x = _audit_count(engine, practice_x)

        client.get(f"/api/patients/{patient_y}/message-threads", headers=_headers(_CLINICIAN_TOKEN))

        after_x = _audit_count(engine, practice_x)
        assert after_x == before_x, "the empty-result listing call wrote an audit row anyway"
