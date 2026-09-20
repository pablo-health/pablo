# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""A patient signs in to a self-hosted deployment, end to end.

The other patient-principal integration tests synthesize the principal through
``dependency_overrides`` — issuance was somebody else's bead and the resolver
was not what they were about. This one is about exactly that: it mints a real
invitation with the adapters an unconfigured deployment can actually turn on,
reads the link and the code back the way the patient would, redeems them over
HTTP, rotates the session, and presents it to an existing patient route.

Nothing is overridden. The link comes out of the email the SMTP sender handed
its client; the code comes out of the console gateway's log record; the
resolver that answers the last request is the one the app registers at
startup; and the rows behind all of it are in a practice schema built by
``create_practice_schema``.

**What the SMTP double does and does not prove.** It stands in at the
``smtplib.SMTP`` boundary, so everything above it is real: the settings
selector, the adapter, the message the sender builds, and the fact that a
failure to deliver is a refusal. It does not exercise the socket or the
STARTTLS handshake — the local stack's mail server does that, against the same
sender, for the mail the product already sends.

Run: ``make test-integration``.
"""

from __future__ import annotations

import logging
import os
import smtplib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from email.message import EmailMessage

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
_SCHEMA = f"practice_test_portal_{_SUFFIX}"
_PRACTICE_ID = f"practice-portal-{_SUFFIX}"
_CLINICIAN = str(uuid.uuid4())

_PATIENT_EMAIL = "patient@example.test"
_PATIENT_PHONE = "+15005550006"
_SIGNING_KEY = "portal-integration-signing-key-not-a-real-secret"
_PORTAL_ORIGIN = "https://portal.example.test"

REDEEM_URL = "/api/patient/auth/redeem"
REFRESH_URL = "/api/patient/auth/refresh"
PATIENT_ROUTE = "/api/patient/intake/form"


class _RecordingSmtp:
    """Stands in for ``smtplib.SMTP`` and keeps what it was asked to send.

    Class-level storage because the sender constructs a fresh client per send
    and the test reads the result afterwards. The handshake and the
    credentials are accepted and discarded — what this double is here to
    capture is the message.
    """

    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, host: str, port: int, **_options: Any) -> None:
        self.host = host
        self.port = port

    def starttls(self, **_options: Any) -> None:
        return None

    def login(self, *_credentials: str) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        type(self).sent.append(message)

    def quit(self) -> None:
        return None


@pytest.fixture(scope="module", autouse=True)
def _app_imported() -> None:
    """Import the app before any test's log capture is installed.

    ``app.main`` configures logging at import, which replaces the root
    handlers — including the one ``caplog`` installs at test setup. Importing
    it from inside a test therefore silently empties the capture that same
    test is about to read the step-up code out of. A module-scoped fixture
    runs before the function-scoped ``caplog``, so the reconfiguration
    happens first and once.
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


@pytest.fixture(scope="module")
def practice(engine: Engine) -> Iterator[str]:
    """A provisioned practice with one patient who has both channels on file."""
    from app.db.platform_models import PlatformUserRow, PracticeRow  # noqa: PLC0415
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA)

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as session:
        session.add(
            PlatformUserRow(
                id=_CLINICIAN,
                email=f"{_PRACTICE_ID}@example.test",
                name="Owner",
                created_at=now,
            )
        )
        session.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Portal Test Practice",
                schema_name=_SCHEMA,
                owner_email=f"{_PRACTICE_ID}@example.test",
                owner_user_id=_CLINICIAN,
                created_at=now,
            )
        )
        session.commit()

    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, email, phone, "
                "created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), 'Ada', 'Tester', 'ada', 'tester', 'active', "
                "0, :e, :ph, now(), now())"
            ),
            {"p": patient_id, "e": _PATIENT_EMAIL, "ph": _PATIENT_PHONE},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:p AS uuid), :u, :u)"
            ),
            {"p": patient_id, "u": _CLINICIAN},
        )

    yield patient_id

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        # The portal address is platform-scoped, so dropping the practice
        # schema does not take it with it.
        conn.execute(
            text("DELETE FROM platform.companion_practice_slugs WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id = CAST(:i AS uuid)"), {"i": _CLINICIAN}
        )


@pytest.fixture
def self_hosted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The configuration a self-hoster would set, and nothing else.

    Both adapters are the ones the engine ships: mail over SMTP, the step-up
    code to the log. No provider is registered through
    ``app.portal.factory``, so what runs here is the settings path.
    """
    from app.portal import factory  # noqa: PLC0415
    from app.rate_limit import reset_portal_limiters  # noqa: PLC0415
    from app.settings import get_settings  # noqa: PLC0415

    _RecordingSmtp.sent.clear()
    factory.reset_delivery_registrations()
    reset_portal_limiters()
    get_settings.cache_clear()

    monkeypatch.setattr(smtplib, "SMTP", _RecordingSmtp)
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", _SIGNING_KEY)
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", _PORTAL_ORIGIN)
    monkeypatch.setenv("PORTAL_INVITE_DELIVERY", "smtp")
    monkeypatch.setenv("PORTAL_SMS_GATEWAY", "console")
    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_USERNAME", "portal")
    monkeypatch.setenv("SMTP_PASSWORD", "portal")
    monkeypatch.setenv("SMTP_FROM", "portal@example.test")
    get_settings.cache_clear()

    yield

    _RecordingSmtp.sent.clear()
    reset_portal_limiters()
    get_settings.cache_clear()


def _issue_invitation(practice_schema: str, patient_id: str) -> None:
    """Mint one invitation exactly as the clinician route does.

    Called directly rather than over HTTP because the clinician half of that
    request is an identity-provider token and a subscription check, neither of
    which is what this module is testing — the route's own behaviour is
    covered in ``backend/tests/test_portal_auth_routes.py``. Everything below
    the route is real: the same factory, the same stores, the same adapters,
    the same transaction.
    """
    from app.db import create_standalone_session  # noqa: PLC0415
    from app.portal.db_store import DbPortalAuthStore, DbPortalSessionStore  # noqa: PLC0415
    from app.portal.factory import (  # noqa: PLC0415
        build_invite_link,
        build_portal_auth_service,
        invite_delivery_from_settings,
        sms_gateway_from_settings,
    )
    from app.portal.practice_routes import ensure_practice_slug  # noqa: PLC0415

    delivery = invite_delivery_from_settings()
    sms = sms_gateway_from_settings()
    delivery.check_ready()
    sms.check_ready()
    # Minted for real against the platform table, the same call the invite
    # route makes — so the link this test follows is addressed the way a real
    # one is, rather than by a constant that could drift from the minter.
    slug = ensure_practice_slug(_PRACTICE_ID).slug

    session = create_standalone_session(practice_schema)
    try:
        service = build_portal_auth_service(
            store=DbPortalAuthStore(session, tenant=practice_schema),
            sessions=DbPortalSessionStore(session),
            sms=sms,
        )
        issued = service.issue_invite(
            patient_id=patient_id, tenant=practice_schema, phone=_PATIENT_PHONE
        )
        delivery.send_invite(
            to_email=_PATIENT_EMAIL,
            link=build_invite_link(slug=slug, token=issued.token),
        )
        session.commit()
    finally:
        session.close()


def _token_from_the_email() -> str:
    """What the patient would copy out of the magic link they were sent."""
    assert _RecordingSmtp.sent, "nothing reached the mail server"
    message = _RecordingSmtp.sent[-1]
    assert message["To"] == _PATIENT_EMAIL
    body = message.get_content()
    assert _PORTAL_ORIGIN in body
    link = next(word for word in body.split() if word.startswith(_PORTAL_ORIGIN))
    return link.split("#token=", 1)[1]


def _code_from_the_log(caplog: pytest.LogCaptureFixture) -> str:
    """What the patient would read off their phone."""
    lines = [r.getMessage() for r in caplog.records if r.name == "app.portal.adapters"]
    assert lines, "the console gateway logged nothing"
    digits = "".join(c for c in lines[-1] if c.isdigit())
    assert len(digits) >= 6, f"no six-digit code in {lines[-1]!r}"
    return digits[:6]


def test_a_self_hosted_deployment_can_sign_a_patient_in(
    engine: Engine,
    practice: str,
    self_hosted: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invite, redeem, rotate, and read a chart — on shipped adapters only."""
    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    patient_id = practice

    with caplog.at_level(logging.INFO, logger="app.portal.adapters"):
        _issue_invitation(_SCHEMA, patient_id)

    token = _token_from_the_email()
    code = _code_from_the_log(caplog)

    client = TestClient(app)

    redeemed = client.post(REDEEM_URL, json={"token": token, "otp": code})
    assert redeemed.status_code == 200, redeemed.text
    session_token = redeemed.json()["session_token"]

    rotated = client.post(REFRESH_URL, json={"session_token": session_token})
    assert rotated.status_code == 200, rotated.text
    live_token = rotated.json()["session_token"]

    # The rotated credential opens a real patient route, through the resolver
    # the app registered at startup. Nothing here is overridden.
    form = client.get(PATIENT_ROUTE, headers={"Authorization": f"Bearer {live_token}"})
    assert form.status_code == 200, form.text
    assert form.json()["identity"]["first_name"] == "Ada"

    # And the one it replaced no longer does: rotation retires what it
    # replaces, which is what stops a copied token outliving a renewal.
    stale = client.get(PATIENT_ROUTE, headers={"Authorization": f"Bearer {session_token}"})
    assert stale.status_code == 401


def test_the_invitation_is_single_use_against_a_real_database(
    engine: Engine,
    practice: str,
    self_hosted: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The attempt counter and the consumed flag are rows, not memory.

    Proven here rather than only against the in-memory store because
    ``increment_attempts`` is an ``UPDATE ... RETURNING`` and ``mark_consumed``
    has to survive the commit that the failure path makes.
    """
    patient_id = practice

    with caplog.at_level(logging.INFO, logger="app.portal.adapters"):
        _issue_invitation(_SCHEMA, patient_id)

    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    token = _token_from_the_email()
    code = _code_from_the_log(caplog)
    wrong = "000000" if code != "000000" else "111111"
    client = TestClient(app)

    # A wrong code costs an attempt, and the cost is durable.
    assert client.post(REDEEM_URL, json={"token": token, "otp": wrong}).status_code == 401
    assert _attempts(engine, token) == 1

    assert client.post(REDEEM_URL, json={"token": token, "otp": code}).status_code == 200
    # Single use: the same link and code together are now just a 401.
    assert client.post(REDEEM_URL, json={"token": token, "otp": code}).status_code == 401


def test_the_kill_switch_stops_a_live_session_immediately(
    engine: Engine,
    practice: str,
    self_hosted: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Revocation is a row, so it takes effect on the next request rather
    than when the token happens to expire."""
    from app.db import create_standalone_session  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415
    from app.portal.db_store import DbPortalSessionStore  # noqa: PLC0415
    from app.utcnow import utc_now  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    patient_id = practice

    with caplog.at_level(logging.INFO, logger="app.portal.adapters"):
        _issue_invitation(_SCHEMA, patient_id)

    client = TestClient(app)
    token = _token_from_the_email()
    code = _code_from_the_log(caplog)
    session_token = client.post(REDEEM_URL, json={"token": token, "otp": code}).json()[
        "session_token"
    ]

    headers = {"Authorization": f"Bearer {session_token}"}
    assert client.get(PATIENT_ROUTE, headers=headers).status_code == 200

    session = create_standalone_session(_SCHEMA)
    try:
        DbPortalSessionStore(session).revoke_all_for_patient(
            patient_id, at=int(utc_now().timestamp())
        )
        session.commit()
    finally:
        session.close()

    assert client.get(PATIENT_ROUTE, headers=headers).status_code == 401


def _attempts(engine: Engine, token: str) -> int:
    """The stored attempt count for the challenge this token names."""
    from app.portal import tokens  # noqa: PLC0415

    jti = tokens.verify_invite_token(signing_key=_SIGNING_KEY, token=token).jti
    with engine.connect() as conn:
        return int(
            conn.execute(
                # The only interpolation is this module's own schema name.
                text(f"SELECT attempts FROM {_SCHEMA}.companion_auth_challenges WHERE jti = :j"),  # noqa: S608
                {"j": jti},
            ).scalar_one()
        )
