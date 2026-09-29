# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Account recovery finds a real chart as the role the app runs as.

Recovery looks a patient up by email address before anybody is signed in, and
``patients`` is row-scoped. Run as a role that bypasses row-level security —
a superuser, as a local compose database is — an unarmed read sees every
chart and recovery looks fine. Run as the non-superuser role this suite
provisions (and production runs), the same read sees nothing, and recovery
answers its uniform 202 while sending nobody anything.

These tests run the real route over HTTP against a provisioned practice, with
nothing overridden but the last hop of the mail (an ``smtplib.SMTP`` double)
and the step-up gateway (the console one). They prove the lookup finds a
chart owned by the practice owner and one owned by another clinician of the
practice, that two charts on one address still mint nothing, and that the
lookup leaves no clinician scope behind on the session.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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
_SCHEMA = f"practice_test_recover_{_SUFFIX}"
_PRACTICE_ID = f"practice-recover-{_SUFFIX}"
_OWNER = str(uuid.uuid4())
_MEMBER = str(uuid.uuid4())
_OWNER_EMAIL = f"owner-{_SUFFIX}@example.test"
_MEMBER_EMAIL = f"member-{_SUFFIX}@example.test"
_OWNER_NAME = "Dana Whitfield"
_MEMBER_NAME = "Dr. Sam Okafor"
_SIGNING_KEY = "portal-recovery-signing-key-not-a-real-secret"
_PORTAL_ORIGIN = "https://portal.example.test"


class _RecordingSmtp:
    """Stands in for ``smtplib.SMTP`` and keeps what it was asked to send."""

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
    """Import the app once, before any test configures settings around it."""
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
def slug(engine: Engine) -> Iterator[str]:
    """A provisioned practice with an owner and one other clinician."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticePortalSettingsRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from app.portal.practice_routes import ensure_practice_slug  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA)

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as session:
        for user_id, email, name in (
            (_OWNER, _OWNER_EMAIL, _OWNER_NAME),
            (_MEMBER, _MEMBER_EMAIL, _MEMBER_NAME),
        ):
            session.add(PlatformUserRow(id=user_id, email=email, name=name, created_at=now))
        session.flush()
        session.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Recovery Test Practice",
                schema_name=_SCHEMA,
                owner_email=_OWNER_EMAIL,
                owner_user_id=_OWNER,
                created_at=now,
            )
        )
        session.flush()
        # The member belongs to the practice the way every clinician does:
        # through the address they sign in with. The owner is found through
        # the practice row instead, so neither path is the only one tested.
        session.add(
            EmailTenantMappingRow(
                email=_MEMBER_EMAIL, tenant_id=_SCHEMA, practice_id=_PRACTICE_ID, created_at=now
            )
        )
        # Recovery resolves only a practice that offers the portal.
        session.add(
            PracticePortalSettingsRow(
                practice_id=_PRACTICE_ID, enabled=True, decided_at=now, updated_at=now
            )
        )
        session.commit()

    yield ensure_practice_slug(_PRACTICE_ID).slug

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.execute(
            text("DELETE FROM platform.companion_practice_slugs WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(
            text("DELETE FROM platform.email_tenant_mappings WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(
            text("DELETE FROM platform.practice_portal_settings WHERE practice_id = :i"),
            {"i": _PRACTICE_ID},
        )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        for user_id in (_OWNER, _MEMBER):
            conn.execute(
                text("DELETE FROM platform.users WHERE id = CAST(:i AS uuid)"), {"i": user_id}
            )


@pytest.fixture
def self_hosted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Mail over SMTP (to the double), the step-up code to the log."""
    from app.portal import factory  # noqa: PLC0415
    from app.rate_limit import reset_portal_limiters  # noqa: PLC0415
    from app.settings import get_settings  # noqa: PLC0415

    _RecordingSmtp.sent.clear()
    factory.reset_delivery_registrations()
    reset_portal_limiters()
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


def _chart(engine: Engine, *, email: str, clinician: str, invited: bool) -> str:
    """A live chart on *clinician*'s caseload, written as that clinician.

    *invited* leaves an unspent invitation behind, which is what gives the
    patient portal access for recovery to restore.
    """
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, email, phone, "
                "created_at, updated_at) "
                "VALUES (CAST(:p AS uuid), 'Ada', 'Tester', 'ada', 'tester', 'active', "
                "0, :e, '+15005550006', now(), now())"
            ),
            {"p": patient_id, "e": email},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:p AS uuid), :u, :u)"
            ),
            {"p": patient_id, "u": clinician},
        )
        if invited:
            conn.execute(
                text(
                    "INSERT INTO companion_auth_challenges "
                    "(jti, patient_id, otp_hash, created_at, expires_at, attempts, consumed) "
                    "VALUES (:j, CAST(:p AS uuid), NULL, now(), :x, 0, false)"
                ),
                {
                    "j": uuid.uuid4().hex,
                    "p": patient_id,
                    "x": datetime.now(UTC) + timedelta(days=7),
                },
            )
    return patient_id


def _co_treat(engine: Engine, *, patient_id: str, clinician: str) -> None:
    """Add *clinician* to a chart as a co-treater — a grant, but not primary."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by) "
                "VALUES (CAST(:p AS uuid), :u, 'co_treating', :u)"
            ),
            {"p": patient_id, "u": clinician},
        )


def _address(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:8]}@example.test"


def _recover(slug: str, email: str) -> Any:
    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    return TestClient(app).post(f"/api/portal/practices/{slug}/recover", json={"email": email})


def _mail_to(address: str) -> list[EmailMessage]:
    return [m for m in _RecordingSmtp.sent if m["To"] == address]


def test_the_runtime_role_cannot_read_charts_unarmed(engine: Engine, slug: str) -> None:
    """The premise: as this suite's role, an unarmed read of ``patients``
    sees nothing. Without it the tests below prove nothing."""
    email = _address("premise")
    _chart(engine, email=email, clinician=_OWNER, invited=True)
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        visible = conn.execute(
            text("SELECT count(*) FROM patients WHERE lower(email) = :e"), {"e": email}
        ).scalar_one()
    assert visible == 0


def test_recovery_finds_the_patient_and_emails_the_link(
    engine: Engine, slug: str, self_hosted: None
) -> None:
    email = _address("owner-patient")
    _chart(engine, email=email, clinician=_OWNER, invited=True)

    response = _recover(slug, email)

    assert response.status_code == 202
    assert response.content == b""
    sent = _mail_to(email)
    assert len(sent) == 1, "recovery sent no link to a patient who has access"
    assert f"{_PORTAL_ORIGIN}/portal/{slug}#invite=" in sent[0].get_content()


def test_a_chart_on_another_clinicians_caseload_is_found(
    engine: Engine, slug: str, self_hosted: None
) -> None:
    """Not only the owner's patients: a clinician who joined the practice
    has a caseload recovery has to reach too."""
    email = _address("member-patient")
    _chart(engine, email=email, clinician=_MEMBER, invited=True)

    assert _recover(slug, email).status_code == 202
    assert len(_mail_to(email)) == 1


def test_two_charts_on_one_address_mint_nothing(
    engine: Engine, slug: str, self_hosted: None
) -> None:
    """A shared family address is a guess about who is asking. The second
    chart was never invited and sits on another clinician's caseload, and it
    still counts."""
    email = _address("shared")
    _chart(engine, email=email, clinician=_OWNER, invited=True)
    _chart(engine, email=email, clinician=_MEMBER, invited=False)

    response = _recover(slug, email)

    assert response.status_code == 202
    assert response.content == b""
    assert _mail_to(email) == []


def test_an_address_nobody_has_gets_the_same_answer_and_no_mail(
    slug: str, self_hosted: None
) -> None:
    email = _address("stranger")

    response = _recover(slug, email)

    assert response.status_code == 202
    assert response.content == b""
    assert _mail_to(email) == []


def test_the_lookup_leaves_no_clinician_scope_behind(engine: Engine, slug: str) -> None:
    """Each clinician is armed for one statement. Afterwards the session is
    as unscoped as it was, so nothing later in the request reads as them."""
    from app.portal.db_store import DbPortalAuthStore  # noqa: PLC0415
    from app.portal.recovery_gateway import DbRecoveryGateway  # noqa: PLC0415

    email = _address("scope")
    patient_id = _chart(engine, email=email, clinician=_OWNER, invited=True)

    with DbRecoveryGateway().open(_SCHEMA, None) as work:
        assert isinstance(work.challenges, DbPortalAuthStore)
        session = work.challenges._session
        before = session.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        ).scalar_one()
        # Measured from a clean start, so the assertion below is about the
        # lookup and not about whatever an earlier test left armed.
        assert not before, f"the session started armed as {before!r}"

        target = work.find_patient(email)
        assert target is not None
        assert target.patient_id == patient_id

        after = session.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        ).scalar_one()
        # NULL before (never set on this connection) and '' after (set inside
        # the savepoint, then reverted) are the same thing to every policy:
        # no principal. Anything else is the lookup's doing.
        assert (after or "") == (before or ""), "the lookup changed who the session reads as"
        assert not after
        visible = session.execute(
            text("SELECT count(*) FROM patients WHERE id = CAST(:p AS uuid)"), {"p": patient_id}
        ).scalar_one()
        assert visible == 0


# ---------------------------------------------------------------------------
# The invitation names the client's own clinician
# ---------------------------------------------------------------------------


def test_the_recovery_email_names_the_practice_and_invites_nobody(
    engine: Engine, slug: str, self_hosted: None
) -> None:
    """The client asked for this link, so the email says so — through the
    real route, practice lookup and mail sender."""
    from app.portal.practice_routes import practice_address_for_schema  # noqa: PLC0415

    address = practice_address_for_schema(_SCHEMA)
    assert address is not None
    email = _address("recovered")
    _chart(engine, email=email, clinician=_MEMBER, invited=True)

    assert _recover(slug, email).status_code == 202

    [sent] = _mail_to(email)
    assert sent["Subject"] == f"Your sign-in link for {address.display_name}"
    body = sent.get_content()
    assert body.startswith(
        f"Here's a new link to sign in to the patient portal for {address.display_name}."
    )
    assert "invited" not in body
    assert _MEMBER_NAME not in body
    assert "{{" not in body


def test_the_primary_clinician_is_named_not_a_co_treater(engine: Engine, slug: str) -> None:
    from app.portal.clinicians import primary_clinician_name  # noqa: PLC0415

    patient_id = _chart(engine, email=_address("co-treated"), clinician=_OWNER, invited=False)
    _co_treat(engine, patient_id=patient_id, clinician=_MEMBER)

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        assert primary_clinician_name(session, _SCHEMA, patient_id) == _OWNER_NAME


def test_a_sender_who_is_not_the_primary_still_gets_the_primarys_name(
    engine: Engine, slug: str
) -> None:
    """Front-desk staff send invitations for clients who are not theirs. The
    lookup finds the primary anyway, and leaves the sender reading as
    themselves afterwards."""
    from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
    from app.portal.clinicians import primary_clinician_name  # noqa: PLC0415

    patient_id = _chart(engine, email=_address("front-desk"), clinician=_OWNER, invited=False)

    with Session(engine) as session:
        set_tenant_schema(session, _SCHEMA)
        arm_current_user_id(session, _MEMBER)

        assert primary_clinician_name(session, _SCHEMA, patient_id) == _OWNER_NAME

        still = session.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        ).scalar_one()
        assert still == _MEMBER, "the lookup changed who the sender reads as"
        session.rollback()


def test_a_chart_with_no_primary_clinician_has_no_name(engine: Engine, slug: str) -> None:
    from app.portal.clinicians import primary_clinician_name  # noqa: PLC0415

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        assert primary_clinician_name(session, _SCHEMA, str(uuid.uuid4())) is None
