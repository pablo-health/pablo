# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Portal routes, one client against another client's objects.

One practice, two clinicians. Avery (the practice owner) holds the
``patient_clinicians`` grant on Pat and on Quinn; Blake holds the grant on
Rowan. Pat owns every object below. Each other client is tried against them:

* **Quinn, the same clinician's client.** The worst case: anything a check
  inherits from the clinician side (a grant, a caseload, the clinician-armed
  session the booking routes run on) is satisfied for Quinn exactly as it is
  for Pat, so only the patient-principal scoping can tell them apart.
* **Rowan, another clinician's client.** The ordinary case.

Every client signs in for real, the way ``test_portal_sign_in_e2e.py`` does:
an invitation minted by the engine's own service, a code read off the console
SMS gateway, a redemption over HTTP. The bearer token that comes back is what
every request presents, so the resolver, the arming of
``app.current_patient_id`` and the row policies are the ones that ship, on a
practice schema built by ``create_practice_schema`` and a connection subject
to row security. Two things are stood in for, neither of them under test:
object storage (an in-memory provider, so a signed URL can be minted and an
upload checked) and the chat model (the fake gateway).

Three kinds of case:

* **Routes that name Pat's object in the path** (a message thread, a
  document, a conversation, an appointment). Pat calls each one first and
  gets a 2xx; then the other client calls it on Pat's object and gets 404,
  never 403 or 409. After every attempt the rows Pat and the caller can each
  see, and the practice audit trail, are exactly as they were.
* **Lists.** Pat's list carries the object; the other client's list does not.
* **Attachments.** There is no attachment route on its own: a message's
  attachment is a document, fetched through the documents route above and
  named by id when a message is sent. Naming Pat's document on the other
  client's message is refused with the same answer as an id that never
  existed, and nothing is written.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.auth.patient_context import PatientContext
    from app.services.chat_llm_gateway import FakeChatLLMGateway
    from app.services.patient_documents_service import PatientDocumentsService
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_xpp_{_SUFFIX}"
_PRACTICE_ID = f"practice-xpp-{_SUFFIX}"
_AVERY = str(uuid.uuid4())  # the practice owner; Pat's and Quinn's clinician
_BLAKE = str(uuid.uuid4())  # Rowan's clinician
_OWNER_EMAIL = f"{_PRACTICE_ID}@example.test"
_BLAKE_EMAIL = f"blake-{_SUFFIX}@example.test"

_SIGNING_KEY = "cross-patient-portal-signing-key-not-a-real-secret"
_PORTAL_ORIGIN = "https://portal.example.test"
_BUCKET = "cross-patient-portal-test-bucket"
_PDF = b"%PDF-1.4\n%cross-patient-portal\n"

# Distinctive enough that a substring match in a response is proof.
_PAT_SUBJECT = "pat-thread-subject-only-pat-may-read"
_PAT_BODY = "pat-message-body-only-pat-may-read"
_PAT_REPLY = "practice-reply-to-pat-only-pat-may-read"
_PAT_FILENAME = "pat-letter-only-pat-may-read.pdf"
_PAT_CHAT_TITLE = "pat-conversation-title-only-pat-may-read"
_ASSISTANT_TEXT = "Thanks for telling me how this week went."

_SESSION_TYPE = "Therapy session"
_TZ_NAME = "America/New_York"
_TZ = ZoneInfo(_TZ_NAME)


@dataclass(frozen=True)
class _Portal:
    pat: str
    quinn: str
    rowan: str
    tokens: dict[str, str]


#: The two arrangements: which client tries Pat's objects.
_INTRUDERS = ("same_clinician", "other_clinician")


def _intruder(portal: _Portal, arrangement: str) -> str:
    return portal.quinn if arrangement == "same_clinician" else portal.rowan


# ---------------------------------------------------------------------------
# Module-scoped infra
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars as this module found them."""
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    tokens = [
        (var, var.set(var.get()))
        for var in (_current_user_id, _current_patient_id, _current_tenant_schema)
    ]
    yield
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.fixture(scope="module")
def _portal_settings() -> Iterator[None]:
    """What a self-hoster sets to turn the portal on, for this whole module.

    Module-scoped, not per test: the session tokens minted at sign-in are
    verified against the signing key on every later request.
    """
    from app.portal import factory  # noqa: PLC0415
    from app.rate_limit import reset_portal_limiters  # noqa: PLC0415
    from app.settings import get_settings  # noqa: PLC0415

    with pytest.MonkeyPatch.context() as mp:
        factory.reset_delivery_registrations()
        mp.setenv("PORTAL_TOKEN_SIGNING_KEY", _SIGNING_KEY)
        mp.setenv("PORTAL_WEB_BASE_URL", _PORTAL_ORIGIN)
        mp.setenv("PORTAL_SMS_GATEWAY", "console")
        get_settings.cache_clear()
        reset_portal_limiters()
        yield
    reset_portal_limiters()
    get_settings.cache_clear()


def _target_date() -> date:
    """A Monday two weeks out, in the owner's zone (as the booking suite)."""
    day = (datetime.now(UTC) + timedelta(days=14)).astimezone(_TZ).date()
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _at(hour: int) -> datetime:
    """The UTC instant of a wall-clock hour in the owner's timezone."""
    return datetime.combine(_target_date(), time(hour, 0), tzinfo=_TZ).astimezone(UTC)


def _seed_practice(engine: Engine) -> tuple[str, str, str]:
    """The practice, both clinicians, three clients and Avery's diary."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticePortalSettingsRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    # The row policies call ``has_patient_access`` unqualified; see
    # ``test_patients_api_e2e.tenant_schema`` for why the pool is warmed.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA)

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        s.add(PlatformUserRow(id=_AVERY, email=_OWNER_EMAIL, name="Avery", created_at=now))
        s.add(PlatformUserRow(id=_BLAKE, email=_BLAKE_EMAIL, name="Blake", created_at=now))
        s.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Cross Patient Portal Practice",
                schema_name=_SCHEMA,
                owner_email=_OWNER_EMAIL,
                owner_user_id=_AVERY,
                created_at=now,
            )
        )
        s.add(
            PracticePortalSettingsRow(
                practice_id=_PRACTICE_ID, enabled=True, decided_at=now, updated_at=now
            )
        )
        # Blake is a member, as an invited clinician is: the booking routes
        # find a client's clinician among the practice's members.
        s.add(
            EmailTenantMappingRow(
                email=_BLAKE_EMAIL, tenant_id=_PRACTICE_ID, practice_id=_PRACTICE_ID, created_at=now
            )
        )
        s.commit()

    pat, quinn, rowan = (str(uuid.uuid4()) for _ in range(3))
    clients = (
        (pat, "Pat", _AVERY, "+15005550101"),
        (quinn, "Quinn", _AVERY, "+15005550102"),
        (rowan, "Rowan", _BLAKE, "+15005550103"),
    )
    for patient_id, first, clinician, phone in clients:
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician}
            )
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, email, phone, "
                    "created_at, updated_at) "
                    "VALUES (CAST(:p AS uuid), :f, 'Client', lower(:f), 'client', 'active', "
                    "0, :e, :ph, now(), now())"
                ),
                {"p": patient_id, "f": first, "e": f"{first.lower()}@example.test", "ph": phone},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:p AS uuid), :u, :u)"
                ),
                {"p": patient_id, "u": clinician},
            )
    _seed_diary(engine)
    return pat, quinn, rowan


def _seed_diary(engine: Engine) -> None:
    """Avery's zone, a weekday 09:00-17:00 on the hour, and a bookable type.

    The booking routes run on a session armed as the client's own clinician,
    Avery for Pat, so the reschedule control needs an hour Avery offers.
    """
    from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
    from app.models.user import UserPreferences  # noqa: PLC0415
    from app.repositories.postgres.availability_rule import (  # noqa: PLC0415
        PostgresAvailabilityRuleRepository,
    )
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from app.scheduling_engine.models.availability import (  # noqa: PLC0415
        AvailabilityRule,
        EnforcementLevel,
        RuleType,
    )
    from app.scheduling_engine.services.scheduling_policy import (  # noqa: PLC0415
        update_policy,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(bind=engine) as s:
        PostgresUserRepository(s).save_preferences(_AVERY, UserPreferences(timezone=_TZ_NAME))
        s.commit()

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        set_tenant_schema(s, _SCHEMA)
        arm_current_user_id(s, _AVERY)
        repo = PostgresAvailabilityRuleRepository(s)
        for rule_type, params in (
            (
                RuleType.WORKING_HOURS,
                {"day_of_week": _target_date().weekday(), "start": "09:00", "end": "17:00"},
            ),
            (RuleType.SESSION_DEFAULTS, {"alignment": "hour"}),
        ):
            repo.create(
                AvailabilityRule(
                    id=str(uuid.uuid4()),
                    user_id=_AVERY,
                    rule_type=rule_type.value,
                    enforcement=EnforcementLevel.SOFT.value,
                    params=params,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.execute(
            text(
                "INSERT INTO appointment_types (id, user_id, name, duration_minutes, "
                "self_bookable, created_at, updated_at) "
                "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :n, 50, true, now(), now())"
            ),
            {"i": str(uuid.uuid4()), "u": _AVERY, "n": _SESSION_TYPE},
        )
        update_policy(
            s,
            {
                "self_book_existing": True,
                "self_book_mode": "auto",
                "min_notice_hours": 1,
                "max_horizon_days": 60,
                "cancel_cutoff_hours": 24,
                "reschedule_cutoff_hours": 24,
            },
        )
        s.commit()


class _OtpCapture(logging.Handler):
    """Keeps what the console SMS gateway logs: the code a phone would show."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _sign_in(client: TestClient, patient_id: str) -> str:
    """Invite, ask for a code, redeem it: the bearer token a client holds.

    The invitation is minted by the service the clinician route calls (that
    route's identity-provider half is not what this module is about); the
    code comes back through the shipped console gateway and the redemption
    goes over HTTP, so the session is a real, stepped-up one.
    """
    from app.db import create_standalone_session  # noqa: PLC0415
    from app.portal.db_store import DbPortalAuthStore, DbPortalSessionStore  # noqa: PLC0415
    from app.portal.factory import (  # noqa: PLC0415
        build_portal_auth_service,
        sms_gateway_from_settings,
    )
    from app.portal.practice_routes import ensure_practice_slug  # noqa: PLC0415
    from app.rate_limit import reset_portal_limiters  # noqa: PLC0415

    reset_portal_limiters()
    ensure_practice_slug(_PRACTICE_ID)
    session = create_standalone_session(_SCHEMA)
    try:
        issued = build_portal_auth_service(
            store=DbPortalAuthStore(session, tenant=_SCHEMA),
            sessions=DbPortalSessionStore(session),
            sms=sms_gateway_from_settings(),
        ).issue_invite(patient_id=patient_id, tenant=_SCHEMA)
        session.commit()
    finally:
        session.close()

    gateway_log = logging.getLogger("app.portal.adapters")
    capture = _OtpCapture()
    previous_level = gateway_log.level
    gateway_log.addHandler(capture)
    gateway_log.setLevel(logging.INFO)
    try:
        asked = client.post("/api/patient/auth/request-code", json={"token": issued.token})
    finally:
        gateway_log.removeHandler(capture)
        gateway_log.setLevel(previous_level)
    assert asked.status_code == 202, asked.text
    assert capture.lines, "the console gateway logged no code"
    code = "".join(c for c in capture.lines[-1] if c.isdigit())[:6]

    redeemed = client.post("/api/patient/auth/redeem", json={"token": issued.token, "otp": code})
    assert redeemed.status_code == 200, redeemed.text
    return str(redeemed.json()["session_token"])


def _client_for(app: FastAPI) -> TestClient:
    """A test client whose every request starts from an empty context.

    The diary seeding arms Avery in the pytest thread, which sets the request
    ContextVars there, and TestClient hands the calling thread's context to
    the app. A patient request would then carry a clinician id no production
    request can.
    """
    from fastapi.testclient import TestClient  # noqa: PLC0415

    class _Fresh(TestClient):
        def request(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            return contextvars.Context().run(super().request, *args, **kwargs)

    # https: SecurityHeadersMiddleware refuses plain HTTP on PHI routes.
    return _Fresh(app, base_url="https://testserver", raise_server_exceptions=False)


@pytest.fixture(scope="module")
def _portal_chat_mounted(fastapi_app: FastAPI) -> Iterator[None]:
    """The client-facing chat routes, as a deployment with them switched on serves them.

    ``app.main`` mounts them at import when ``enable_patient_portal_chat`` is
    on, and in a full run whichever module imports the app first decides
    that. So they are mounted here, the same router the same way, when the
    app came up without them, and taken off again afterwards so no later
    module sees a route its deployment would not have.
    """
    from app.routes import patient_chat  # noqa: PLC0415

    routes = fastapi_app.router.routes
    if any(getattr(r, "path", "").startswith(patient_chat.router.prefix) for r in routes):
        yield
        return
    before = list(routes)
    fastapi_app.include_router(patient_chat.router)
    try:
        yield
    finally:
        routes[:] = before


@pytest.fixture(scope="module")
def portal(
    engine: Engine, fastapi_app: FastAPI, _portal_settings: None, _portal_chat_mounted: None
) -> Iterator[_Portal]:
    pat, quinn, rowan = _seed_practice(engine)
    client = _client_for(fastapi_app)
    tokens = {pid: _sign_in(client, pid) for pid in (pat, quinn, rowan)}

    yield _Portal(pat=pat, quinn=quinn, rowan=rowan, tokens=tokens)

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        for table in (
            "companion_practice_slugs",
            "practice_portal_settings",
        ):
            conn.execute(
                text(f"DELETE FROM platform.{table} WHERE practice_id = :i"),  # noqa: S608
                {"i": _PRACTICE_ID},
            )
        conn.execute(
            text("DELETE FROM platform.email_tenant_mappings WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
            {"a": _AVERY, "b": _BLAKE},
        )


# ---------------------------------------------------------------------------
# Per-test client
# ---------------------------------------------------------------------------


def _memory_storage() -> Any:
    """Object storage that always holds a small, genuine PDF.

    Not under test: the routes below decide access from the database before
    storage is asked anything. It exists so a control can mint a download URL
    and finish an upload.
    """
    from app.services.file_storage import FileStorageProvider, UploadTarget  # noqa: PLC0415

    class _MemoryStorage(FileStorageProvider):
        def make_upload_target(self, **_kw: Any) -> UploadTarget:
            return UploadTarget(url="https://storage.example.test/upload", method="PUT")

        def make_download_url(self, *, object_name: str, **_kw: Any) -> str:  # type: ignore[override]
            return f"https://storage.example.test/{object_name}"

        def fetch_metadata(self, **_kw: Any) -> tuple[int, str | None]:
            return len(_PDF), "application/pdf"

        def download_bytes(self, **_kw: Any) -> bytes:
            return _PDF

        def upload_bytes(self, **_kw: Any) -> None:
            return None

        def upload_stream(self, **_kw: Any) -> None:
            return None

        def delete(self, **_kw: Any) -> None:
            return None

        def list_names(self, **_kw: Any) -> list[str]:
            return []

    return _MemoryStorage()


@pytest.fixture
def fake_gateway() -> FakeChatLLMGateway:
    from app.services.chat_llm_gateway import (  # noqa: PLC0415
        FakeChatLLMGateway,
        StreamEvent,
    )

    return FakeChatLLMGateway(
        script=[
            StreamEvent(delta=_ASSISTANT_TEXT),
            StreamEvent(finish_reason="stop", output_tokens=8),
        ]
    )


@pytest.fixture
def client(
    fastapi_app: FastAPI, portal: _Portal, fake_gateway: FakeChatLLMGateway
) -> Iterator[TestClient]:
    from app.auth.patient_context import get_patient_context  # noqa: PLC0415
    from app.rate_limit import (  # noqa: PLC0415
        get_chat_send_limiter,
        get_patient_document_init_limiter,
        get_patient_message_send_limiter,
    )
    from app.repositories import get_patient_document_repository  # noqa: PLC0415
    from app.routes.chat import get_chat_llm_gateway  # noqa: PLC0415
    from app.routes.patient_documents import (  # noqa: PLC0415
        get_patient_documents_service_for_patient,
    )
    from app.services.patient_documents_service import (  # noqa: PLC0415
        PatientDocumentsService,
    )
    from app.settings import get_settings  # noqa: PLC0415
    from fastapi import Depends  # noqa: PLC0415

    storage = _memory_storage()
    settings = get_settings().model_copy(update={"patient_documents_gcs_bucket": _BUCKET})

    def _documents_service(
        patient: PatientContext = Depends(get_patient_context),
    ) -> PatientDocumentsService:
        # The real repository on the patient-armed session; only the storage
        # behind it is stood in for.
        return PatientDocumentsService(
            repo=get_patient_document_repository(),
            settings=settings,
            storage=storage,
            tenant_id=patient.practice_schema,
        )

    for limiter in (
        get_chat_send_limiter(),
        get_patient_document_init_limiter(),
        get_patient_message_send_limiter(),
    ):
        limiter.reset()
    fastapi_app.dependency_overrides[get_patient_documents_service_for_patient] = _documents_service
    fastapi_app.dependency_overrides[get_chat_llm_gateway] = lambda: fake_gateway
    try:
        yield _client_for(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _bearer(portal: _Portal, patient_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {portal.tokens[patient_id]}"}


# ---------------------------------------------------------------------------
# Pat's objects, made through Pat's own routes
# ---------------------------------------------------------------------------


def _new_thread(client: TestClient, portal: _Portal, patient_id: str) -> str:
    """A thread the client started, with one practice reply waiting unread.

    The reply is what ``/read`` would stamp, so a read that landed on the
    wrong caller's thread changes a row rather than nothing.
    """
    resp = client.post(
        "/api/patient/messages/threads",
        json={"subject": _PAT_SUBJECT, "body": _PAT_BODY},
        headers=_bearer(portal, patient_id),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _add_practice_reply(engine: Engine, patient_id: str, thread_id: str) -> None:
    clinician = _AVERY  # Pat's clinician
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, "
                "created_at) VALUES (CAST(:i AS uuid), CAST(:t AS uuid), CAST(:p AS uuid), "
                "'clinician', :b, now())"
            ),
            {"i": str(uuid.uuid4()), "t": thread_id, "p": patient_id, "b": _PAT_REPLY},
        )


def _pat_thread(client: TestClient, engine: Engine, portal: _Portal) -> str:
    thread_id = _new_thread(client, portal, portal.pat)
    _add_practice_reply(engine, portal.pat, thread_id)
    return thread_id


def _upload(
    client: TestClient, portal: _Portal, patient_id: str, *, finish: bool, category: str = "message"
) -> str:
    started = client.post(
        "/api/patient/documents/init",
        json={
            "filename": _PAT_FILENAME,
            "mime_type": "application/pdf",
            "size_bytes": len(_PDF),
            "category": category,
        },
        headers=_bearer(portal, patient_id),
    )
    assert started.status_code == 201, started.text
    document_id = str(started.json()["document_id"])
    if finish:
        done = client.post(
            f"/api/patient/documents/{document_id}/finalize",
            headers=_bearer(portal, patient_id),
        )
        assert done.status_code == 200, done.text
    return document_id


def _pat_document(client: TestClient, engine: Engine, portal: _Portal) -> str:
    return _upload(client, portal, portal.pat, finish=True)


def _pat_pending_document(client: TestClient, engine: Engine, portal: _Portal) -> str:
    return _upload(client, portal, portal.pat, finish=False)


def _pat_conversation(client: TestClient, engine: Engine, portal: _Portal) -> str:
    """Pat's conversation with the assistant, one turn in."""
    resp = client.post(
        "/api/patient/chat/conversations",
        json={"title": _PAT_CHAT_TITLE},
        headers=_bearer(portal, portal.pat),
    )
    assert resp.status_code == 201, resp.text
    conversation_id = str(resp.json()["id"])
    turn = client.post(
        f"/api/patient/chat/conversations/{conversation_id}/messages",
        json={"content": "This week was hard."},
        headers=_bearer(portal, portal.pat),
    )
    assert turn.status_code == 200, turn.text
    return conversation_id


_appointment_hours: dict[str, int] = {}


def _clear_appointments(engine: Engine) -> None:
    """Every appointment goes, deleted as the owner whose diary it is."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _AVERY})
        conn.execute(text("DELETE FROM appointments"))


def _seed_appointment(engine: Engine, patient_id: str, start: datetime) -> str:
    """An appointment booked clinician-side on Avery's diary, as the booking
    suite seeds them: through the real ``SchedulingService``."""
    from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.scheduling_engine.services.scheduling import SchedulingService  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(bind=engine) as s:
        set_tenant_schema(s, _SCHEMA)
        arm_current_user_id(s, _AVERY)
        created = SchedulingService(PostgresAppointmentRepository(s)).create_appointment(
            _AVERY,
            data={
                "patient_id": patient_id,
                "title": "Booked",
                "start_at": start,
                "end_at": start + timedelta(minutes=50),
                "duration_minutes": 50,
                "session_type": _SESSION_TYPE,
            },
        )
        s.commit()
    return created.id


def _pat_appointment(client: TestClient, engine: Engine, portal: _Portal) -> str:
    """Pat's appointment. Target and twin take 10:00 and 11:00; the moves go
    to 14:00 (Pat's control) and 15:00 (the other client's attempt)."""
    hour = 11 if _appointment_hours.get("target") else 10
    _appointment_hours["target"] = hour
    return _seed_appointment(engine, portal.pat, _at(hour))


_MAKERS: dict[str, Callable[[TestClient, Engine, _Portal], str]] = {
    "thread": _pat_thread,
    "document": _pat_document,
    "pending_document": _pat_pending_document,
    "conversation": _pat_conversation,
    "appointment": _pat_appointment,
}


# ---------------------------------------------------------------------------
# What a write could have touched
# ---------------------------------------------------------------------------

_PATIENT_TABLES = (
    "patient_message_threads",
    "patient_messages",
    "patient_message_attachments",
    "patient_documents",
    "chat_conversations",
    "chat_messages",
    "appointments",
)


def _footprint(engine: Engine, *patient_ids: str) -> dict[str, Any]:
    """Every row each named client can see, whole, plus the audit trail.

    Read as each client in turn, under the same patient arm a portal request
    gets, so a row the other client's request changed on Pat's side, or wrote
    on its own side, shows up in one view or the other. Whole rows as text,
    so an ``updated_at`` or a ``read_at`` that moved is a difference. The
    audit trail is read under the purge setting, the one that sees every
    actor's rows (``audit_logs`` is FORCE RLS).
    """
    seen: dict[str, Any] = {}
    with engine.connect() as conn:
        for patient_id in patient_ids:
            conn.execute(text(f"SET LOCAL search_path = {_SCHEMA}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_patient_id', :p, true)"), {"p": patient_id}
            )
            for table in _PATIENT_TABLES:
                seen[f"{patient_id}:{table}"] = (
                    conn.execute(text(f"SELECT t::text FROM {table} t ORDER BY 1"))  # noqa: S608
                    .scalars()
                    .all()
                )
            conn.rollback()
        conn.execute(text(f"SET LOCAL search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        seen["audit"] = (
            conn.execute(text("SELECT a::text FROM audit_logs a ORDER BY 1")).scalars().all()
        )
        conn.rollback()
    return seen


# ---------------------------------------------------------------------------
# Routes that name Pat's object in the path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ObjectRoute:
    name: str
    kind: str  # a key of ``_MAKERS``
    method: str
    path: str  # ``{id}`` is Pat's object
    ok: int
    body: dict[str, Any] | Callable[[str], dict[str, Any]] | None = None
    # A control that changes the object runs on a twin, so the other client's
    # attempt meets the target as Pat left it.
    control_on_twin: bool = False


def _move_to(hour: int) -> Callable[[str], dict[str, Any]]:
    return lambda _caller: {"start_at": _at(hour).isoformat()}


_OBJECT_ROUTES = (
    # Messaging.
    _ObjectRoute("thread_get", "thread", "GET", "/api/patient/messages/threads/{id}", 200),
    _ObjectRoute(
        "thread_send",
        "thread",
        "POST",
        "/api/patient/messages/threads/{id}/messages",
        201,
        body={"body": "written by whoever called"},
        control_on_twin=True,
    ),
    _ObjectRoute(
        "thread_read",
        "thread",
        "POST",
        "/api/patient/messages/threads/{id}/read",
        200,
        control_on_twin=True,
    ),
    # Documents (a message attachment is one of these).
    _ObjectRoute("document_file", "document", "GET", "/api/patient/documents/{id}/file", 200),
    _ObjectRoute(
        "document_finalize_pending",
        "pending_document",
        "POST",
        "/api/patient/documents/{id}/finalize",
        200,
        control_on_twin=True,
    ),
    # Finalize is idempotent on a finished upload and returns the document,
    # so a finished one is the case where a missing check would hand it over.
    _ObjectRoute(
        "document_finalize_finished",
        "document",
        "POST",
        "/api/patient/documents/{id}/finalize",
        200,
    ),
    # Chat.
    _ObjectRoute("chat_get", "conversation", "GET", "/api/patient/chat/conversations/{id}", 200),
    _ObjectRoute(
        "chat_rename",
        "conversation",
        "PATCH",
        "/api/patient/chat/conversations/{id}",
        200,
        body={"title": "Renamed by whoever called"},
        control_on_twin=True,
    ),
    _ObjectRoute(
        "chat_archive",
        "conversation",
        "PATCH",
        "/api/patient/chat/conversations/{id}",
        200,
        body={"archive": True},
        control_on_twin=True,
    ),
    _ObjectRoute(
        "chat_delete_archive",
        "conversation",
        "DELETE",
        "/api/patient/chat/conversations/{id}?mode=archive",
        204,
        control_on_twin=True,
    ),
    _ObjectRoute(
        "chat_delete_purge",
        "conversation",
        "DELETE",
        "/api/patient/chat/conversations/{id}?mode=purge",
        204,
        control_on_twin=True,
    ),
    _ObjectRoute(
        "chat_send",
        "conversation",
        "POST",
        "/api/patient/chat/conversations/{id}/messages",
        200,
        body={"content": "Tell me what Pat said."},
        control_on_twin=True,
    ),
    # Booking (the routes that take an appointment id).
    _ObjectRoute(
        "booking_cancel",
        "appointment",
        "POST",
        "/api/patient/booking/{id}/cancel",
        200,
        control_on_twin=True,
    ),
    _ObjectRoute(
        "booking_reschedule",
        "appointment",
        "POST",
        "/api/patient/booking/{id}/reschedule",
        200,
        body=_move_to(14),
        control_on_twin=True,
    ),
)

#: Where the other client's reschedule tries to put Pat's appointment.
_INTRUDER_MOVE = _move_to(15)


def _call(
    client: TestClient, portal: _Portal, route: _ObjectRoute, caller: str, object_id: str
) -> Response:
    body = route.body
    if route.name == "booking_reschedule" and caller != portal.pat:
        body = _INTRUDER_MOVE
    payload = body(caller) if callable(body) else body
    return client.request(
        route.method,
        route.path.format(id=object_id),
        json=payload,
        headers=_bearer(portal, caller),
    )


@pytest.mark.parametrize("arrangement", _INTRUDERS)
@pytest.mark.parametrize("route", _OBJECT_ROUTES, ids=lambda r: r.name)
def test_another_client_cannot_reach_pats_object(  # noqa: PLR0913 — fixtures
    route: _ObjectRoute,
    arrangement: str,
    client: TestClient,
    engine: Engine,
    portal: _Portal,
    fake_gateway: FakeChatLLMGateway,
) -> None:
    if route.kind == "appointment":
        _clear_appointments(engine)
        _appointment_hours.clear()
    make = _MAKERS[route.kind]
    target = make(client, engine, portal)
    twin = make(client, engine, portal) if route.control_on_twin else target

    # Control: the route works for the client whose object it is.
    control = _call(client, portal, route, portal.pat, twin)
    assert control.status_code == route.ok, (route.name, control.status_code, control.text)

    intruder = _intruder(portal, arrangement)
    before = _footprint(engine, portal.pat, intruder)
    # Not vacuous: Pat's view holds the target.
    assert any(target in row for rows in before.values() for row in rows)
    model_calls = len(fake_gateway.calls)

    denied = _call(client, portal, route, intruder, target)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text)
    for secret in (_PAT_SUBJECT, _PAT_BODY, _PAT_REPLY, _PAT_FILENAME, _PAT_CHAT_TITLE):
        assert secret not in denied.text
    # Nothing written: every row Pat and the caller can see is as it was, no
    # audit row of a change, and the model was never called.
    assert _footprint(engine, portal.pat, intruder) == before
    assert len(fake_gateway.calls) == model_calls


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ListRoute:
    name: str
    kind: str
    path: str


_LIST_ROUTES = (
    _ListRoute("threads", "thread", "/api/patient/messages/threads"),
    _ListRoute("documents", "document", "/api/patient/documents"),
    _ListRoute(
        "conversations", "conversation", "/api/patient/chat/conversations?include_archived=true"
    ),
    _ListRoute("appointments", "appointment", "/api/patient/appointments"),
)


@pytest.mark.parametrize("arrangement", _INTRUDERS)
@pytest.mark.parametrize("route", _LIST_ROUTES, ids=lambda r: r.name)
def test_pats_object_is_absent_from_another_clients_list(
    route: _ListRoute,
    arrangement: str,
    client: TestClient,
    engine: Engine,
    portal: _Portal,
) -> None:
    if route.kind == "appointment":
        _clear_appointments(engine)
        _appointment_hours.clear()
    target = _MAKERS[route.kind](client, engine, portal)

    control = client.get(route.path, headers=_bearer(portal, portal.pat))
    assert control.status_code == 200, control.text
    assert target in {str(row["id"]) for row in control.json()["data"]}

    listed = client.get(route.path, headers=_bearer(portal, _intruder(portal, arrangement)))

    assert listed.status_code == 200, listed.text
    assert target not in {str(row["id"]) for row in listed.json()["data"]}
    assert target not in listed.text
    for secret in (_PAT_SUBJECT, _PAT_BODY, _PAT_FILENAME, _PAT_CHAT_TITLE):
        assert secret not in listed.text


# ---------------------------------------------------------------------------
# Attachments: Pat's document named on another client's message
# ---------------------------------------------------------------------------


def _send_with(
    client: TestClient, portal: _Portal, caller: str, thread_id: str, document_id: str
) -> Response:
    return client.post(
        f"/api/patient/messages/threads/{thread_id}/messages",
        json={"body": "see attached", "attachment_ids": [document_id]},
        headers=_bearer(portal, caller),
    )


def _start_with(client: TestClient, portal: _Portal, caller: str, document_id: str) -> Response:
    return client.post(
        "/api/patient/messages/threads",
        json={"subject": "see attached", "body": "see attached", "attachment_ids": [document_id]},
        headers=_bearer(portal, caller),
    )


_ATTACH_ROUTES = ("send_message", "start_thread")


@pytest.mark.parametrize("arrangement", _INTRUDERS)
@pytest.mark.parametrize("via", _ATTACH_ROUTES)
@pytest.mark.parametrize("sent", [False, True], ids=["unsent", "already_sent"])
def test_another_client_cannot_attach_pats_document(  # noqa: PLR0913 — fixtures
    via: str,
    sent: bool,
    arrangement: str,
    client: TestClient,
    engine: Engine,
    portal: _Portal,
) -> None:
    """Refused exactly as an id that never existed is, and nothing written.

    ``already_sent`` is the sharper case: Pat's own retry of that document is
    told it has already been sent, which is true and is Pat's to know. The
    other client must get the not-available answer instead, or the field
    becomes a way to ask which of Pat's documents went out.
    """
    document = _upload(client, portal, portal.pat, finish=True)
    pat_thread = _new_thread(client, portal, portal.pat)
    if sent:
        first = _send_with(client, portal, portal.pat, pat_thread, document)
        assert first.status_code == 201, first.text
        again = _send_with(client, portal, portal.pat, pat_thread, document)
        assert again.status_code == 422, again.text
        assert again.json()["error"]["code"] == "ATTACHMENT_ALREADY_SENT", again.text
    else:
        # Control: the same request shape attaches the file for its owner.
        control = _start_with(
            client, portal, portal.pat, _upload(client, portal, portal.pat, finish=True)
        )
        assert control.status_code == 201, control.text
        assert control.json()["messages"][0]["attachments"], control.text

    intruder = _intruder(portal, arrangement)
    own_thread = _new_thread(client, portal, intruder)

    def attempt(document_id: str) -> Response:
        if via == "send_message":
            return _send_with(client, portal, intruder, own_thread, document_id)
        return _start_with(client, portal, intruder, document_id)

    nobodys = attempt(str(uuid.uuid4()))
    before = _footprint(engine, portal.pat, intruder)

    denied = attempt(document)

    assert denied.status_code == nobodys.status_code == 422, denied.text
    assert denied.json() == nobodys.json()
    assert denied.json()["error"]["code"] == "ATTACHMENT_NOT_AVAILABLE"
    assert _PAT_FILENAME not in denied.text
    assert _footprint(engine, portal.pat, intruder) == before
