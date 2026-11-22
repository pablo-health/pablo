# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Exports, one clinician against another clinician's client.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and wrote everything on his chart: a visit, a charge, a claim, a
handed-in intake form. Blake holds a grant on Yolanda, with a claim and an
intake form of her own, and none on Xavier. Every request runs through the
real app, the real repositories and the real ``has_patient_access``, against
a practice schema provisioned the production way, on a connection subject to
row security. The request's principal is armed as whoever is calling.

Every route here is a download: a copy of the record leaving the product.
None of them changes a row; what each one writes is the audit row that
records the disclosure. So "nothing written" means the audit trail, read
across every actor, is exactly as it was.

Four kinds of case:

* **Routes that take Avery's client or claim by id** (the chart export in
  each format, the intake form export, the superbill, the statement, the
  CMS-1500). Avery calls each one first and gets the document; then Blake
  calls it and gets 404, never 403 or 409, with nothing of Xavier's in the
  body and no audit row.
* **Avery's form on Blake's own client's path.** The intake export takes
  two ids. Blake names Yolanda, whom they may see, and Avery's form id.
  Blake's own form on the same path is the control.
* **The claims CSV**, which takes no id at all: it selects every claim in a
  date range. Blake's file carries Yolanda's claim and nothing of Xavier's,
  and the audit row it writes names Yolanda's claim alone. Avery's file is
  the control, carrying Xavier's claim from the same range.
* **Lookups by an id that names nothing**, so a 404 for Blake is the same
  404 a stranger gets.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import io
import json
import os
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.models import User
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "e2e-cross-clinician-export@example.com"
# user_id columns are native uuid, so the two clinicians are uuids rather
# than the conftest's ``test_user_id`` strings.
_AVERY = "a7e1c0de-0000-4000-8000-0000000000e1"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000e2"

# Distinctive enough that a substring match in a body is proof. Letters only:
# a sentinel made of digits would turn up by chance in ids and timestamps.
_X_LAST = "Quillfeatherx"
_X_MEMBER = "XAVIERMEMBERQZ"
_Y_LAST = "Marrowgatey"
_Y_MEMBER = "YOLANDAMEMBERQZ"

_TAX_ID = "844459714"


@dataclass
class _Seed:
    xavier: str = field(default_factory=lambda: str(uuid.uuid4()))
    yolanda: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_claim: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_claim: str = field(default_factory=lambda: str(uuid.uuid4()))
    x_control: str = field(default_factory=lambda: "XC" + uuid.uuid4().hex[:10].upper())
    y_control: str = field(default_factory=lambda: "YC" + uuid.uuid4().hex[:10].upper())
    x_form: str = field(default_factory=lambda: str(uuid.uuid4()))
    y_form: str = field(default_factory=lambda: str(uuid.uuid4()))
    today: str = ""


@dataclass
class _Caller:
    """Who the next request is from. Every auth override reads this."""

    user: User


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


@pytest.fixture(scope="module", autouse=True)
def _encryption_key() -> Iterator[None]:
    """The tax id is stored encrypted; the superbill and the claim exports decrypt it."""
    from app.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("GOOGLE_CALENDAR_ENCRYPTION_KEY")
    os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = base64.b64encode(os.urandom(32)).decode()
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"]
        else:
            os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A practice provisioned the production way, mapped to ``_TENANT_EMAIL``."""
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    # The row policies call ``has_patient_access`` unqualified; see
    # ``test_patients_api_e2e.tenant_schema`` for why the pool is warmed.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _open_session(engine: Engine, schema: str, user_id: str) -> tuple[Session, tuple[Any, ...]]:
    """An ORM session on the practice schema, armed as ``user_id`` and nobody else.

    All three ContextVars are set, the patient principal to ``None``: the
    ``after_begin`` listener reads them, and a module fixture runs before any
    per-test reset, so whatever an earlier module left armed must not leak in.
    """
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    tokens = (
        _current_tenant_schema.set(schema),
        _current_user_id.set(user_id),
        _current_patient_id.set(None),
    )
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session, tokens


def _close_session(session: Session, tokens: tuple[Any, ...]) -> None:
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    session.close()
    _current_tenant_schema.reset(tokens[0])
    _current_user_id.reset(tokens[1])
    _current_patient_id.reset(tokens[2])


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client and everything on the chart an export reads.

    Each clinician's rows are written as that clinician, so every row passes
    the same policy a request would have to.
    """
    from app.repositories.clinician_profile import ClinicianProfile  # noqa: PLC0415
    from app.repositories.postgres.clinician_profile import (  # noqa: PLC0415
        PostgresClinicianProfileRepository,
    )
    from app.services.practice_billing_profile import update_billing_profile  # noqa: PLC0415

    s = _Seed()
    now = datetime.now(UTC).replace(microsecond=0)
    s.today = now.date().isoformat()
    charts = (
        (_AVERY, s.xavier, "Xavier", _X_LAST, _X_MEMBER, s.x_claim, s.x_control, s.x_form),
        (_BLAKE, s.yolanda, "Yolanda", _Y_LAST, _Y_MEMBER, s.y_claim, s.y_control, s.y_form),
    )
    for clinician, patient, first, last, member, claim_id, control, form in charts:
        _seed_patient(engine, tenant_schema, clinician, patient, first, last)
        session, tokens = _open_session(engine, tenant_schema, clinician)
        try:
            PostgresClinicianProfileRepository(session).create(
                ClinicianProfile(
                    user_id=clinician,
                    practice_id=tenant_schema,
                    npi_number="1111111112",
                    taxonomy_code="101YM0800X",
                )
            )
            if clinician == _AVERY:
                update_billing_profile(
                    session,
                    {
                        "legal_name": "Harbor Light Counseling",
                        "billing_npi": "1234567893",
                        "tax_id": _TAX_ID,
                        "tax_id_type": "ein",
                        "address_line1": "1 Harbor St",
                        "city": "Chicago",
                        "state": "IL",
                        "postal_code": "60601",
                        "phone": "3125550100",
                    },
                )
            _seed_billing(
                session,
                clinician=clinician,
                patient_id=patient,
                first=first,
                last=last,
                member=member,
                claim_id=claim_id,
                control=control,
                now=now,
            )
            session.commit()
        finally:
            _close_session(session, tokens)
        _seed_intake_form(engine, tenant_schema, clinician, patient, form, now)
    return s


def _seed_patient(  # noqa: PLR0913 — one row's columns
    engine: Engine, schema: str, clinician: str, patient: str, first: str, last: str
) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, session_count, "
                "created_at, updated_at, date_of_birth, sex, address_line1, city, "
                "state, postal_code) "
                "VALUES (CAST(:pid AS uuid), :fn, :ln, :fnl, :lnl, 'active', 1, "
                "now(), now(), '1980-01-15', 'F', '12 Harbor Rd', 'Chicago', 'IL', '60601')"
            ),
            {"pid": patient, "fn": first, "ln": last, "fnl": first.lower(), "lnl": last.lower()},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient, "u": clinician},
        )


def _seed_billing(  # noqa: PLR0913 — one chart's billing rows
    session: Session,
    *,
    clinician: str,
    patient_id: str,
    first: str,
    last: str,
    member: str,
    claim_id: str,
    control: str,
    now: datetime,
) -> None:
    """A completed visit, its copay, a plan, and a submitted claim for the visit.

    Through the repositories the billing screens write with. The claim names
    the visit on the date it was held (the practice's day is UTC here), which
    is what lets the superbill render; it carries the client's name and
    member id, which is what a leak in a CSV would show.
    """
    from app.models.coverage import PatientCoverage  # noqa: PLC0415
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from app.repositories.postgres.patient_payment import (  # noqa: PLC0415
        PostgresPatientPaymentRepository,
    )
    from app.scheduling_engine.models.appointment import Appointment  # noqa: PLC0415
    from app.services.coverage_intake import new_payer  # noqa: PLC0415
    from tests.claims_fixtures import claim, line, person, subscriber_snapshot  # noqa: PLC0415

    appointment_id, coverage_id = str(uuid.uuid4()), str(uuid.uuid4())
    PostgresAppointmentRepository(session).create(
        Appointment(
            id=appointment_id,
            user_id=clinician,
            patient_id=patient_id,
            title="Session",
            start_at=now,
            end_at=now + timedelta(minutes=50),
            duration_minutes=50,
            status="completed",
            session_type="Individual therapy",
            provider="zoom",
            video_link="https://meet.example/room",
            place_of_service="10",
            service_code="90837",
            created_at=now,
            updated_at=now,
        )
    )
    PostgresPatientPaymentRepository(session).add_ledger_row(
        patient_id=patient_id,
        kind="copay",
        amount_cents=2500,
        currency="usd",
        user_id=clinician,
        appointment_id=appointment_id,
        method="check",
    )
    payer = PostgresPayerRepository(session).create(
        new_payer(name="Stedi Test Payer", payer_id="STEDI")
    )
    PostgresPatientCoverageRepository(session).create(
        PatientCoverage(
            id=coverage_id,
            patient_id=patient_id,
            payer_id=payer.id,
            member_id=member,
            group_number="G7",
            created_at=now,
            updated_at=now,
        )
    )
    who = person(first_name=first, last_name=last)
    PostgresClaimRepository(session).create(
        claim(
            id=claim_id,
            control_number=control,
            patient_id=patient_id,
            coverage_id=coverage_id,
            payer_id=payer.id,
            state="submitted",
            submitted_at=now,
            created_at=now,
            updated_at=now,
            subscriber_snapshot=subscriber_snapshot(member_id=member, subscriber=who, patient=who),
            lines=[
                line(
                    id=str(uuid.uuid4()),
                    claim_id=claim_id,
                    patient_id=patient_id,
                    appointment_id=appointment_id,
                    line_control_number=f"{control}L1",
                    service_date=now.date(),
                    created_at=now,
                )
            ],
        )
    )


def _seed_intake_form(  # noqa: PLR0913 — one form's rows
    engine: Engine, schema: str, clinician: str, patient: str, form: str, now: datetime
) -> None:
    """A published one-question form, sent by ``clinician`` and handed in."""
    template_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": clinician})
        conn.execute(
            text(
                "INSERT INTO intake_packet_templates (id, name, created_by, created_at) "
                "VALUES (CAST(:tid AS uuid), 'Intake', CAST(:u AS uuid), :now)"
            ),
            {"tid": template_id, "u": clinician, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_versions "
                "(id, template_id, version, published_at, published_by, created_at) "
                "VALUES (CAST(:vid AS uuid), CAST(:tid AS uuid), 1, :now, "
                "CAST(:u AS uuid), :now)"
            ),
            {"vid": version_id, "tid": template_id, "u": clinician, "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO intake_item_definitions "
                "(id, version_id, key, position, item_type, required, label, config) "
                "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'reason', 1, 'reason', "
                "true, NULL, '{}'::jsonb)"
            ),
            {"iid": str(uuid.uuid4()), "vid": version_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_intake_assignments "
                "(id, patient_id, version_id, status, assigned_by, assigned_at, "
                "submitted_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
                "'submitted', CAST(:u AS uuid), :now, :now, :now)"
            ),
            {"id": form, "pid": patient, "vid": version_id, "u": clinician, "now": now},
        )


# ---------------------------------------------------------------------------
# Per-test client: one app, the caller switched per request
# ---------------------------------------------------------------------------


def _user(user_id: str) -> User:
    from app.models import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=_TENANT_EMAIL,
        name="Avery Ames" if user_id == _AVERY else "Blake Byrne",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def caller() -> _Caller:
    return _Caller(user=_user(_AVERY))


@pytest.fixture
def client(  # noqa: PLR0913 — fixture composition mirrors the FastAPI deps
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    caller: _Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    from app.auth.providers import VerifiedIdentity  # noqa: PLC0415
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    _truncate_audit(engine, tenant_schema)

    def _stash_identity(request: Request) -> None:
        # The middleware resolves the practice from this identity's email,
        # through the real lookup; the subject is whoever is calling.
        request.state.verified_identity = VerifiedIdentity(
            provider="test",
            subject_id=caller.user.id,
            email=_TENANT_EMAIL,
            email_verified=True,
            mfa_satisfied=True,
            claims={},
        )

    monkeypatch.setattr("app.db.middleware._verify_and_stash_clinician_identity", _stash_identity)

    def _tenant_context() -> TenantContext:
        # Arm the CALLER, not a fixed user: a row policy evaluated as the
        # wrong clinician would pass or fail for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return TenantContext(
            user_id=caller.user.id,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    def _user_now() -> User:
        # The real user dependency arms the RLS principal at its shared seam
        # (``app.auth.service``), and several of these routes take only
        # repositories, never the tenant context. Overriding it must keep
        # the arming, or the control would read nothing for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return caller.user

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: caller.user.id
    fastapi_app.dependency_overrides[get_current_user] = _user_now
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _user_now
    fastapi_app.dependency_overrides[require_active_subscription] = _user_now
    fastapi_app.dependency_overrides[require_baa_acceptance] = _user_now
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()
        _truncate_audit(engine, tenant_schema)


def _truncate_audit(engine: Engine, schema: str) -> None:
    """Empty the audit trail; keep the seeded charts."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        # audit_logs is append-only; a fixture reset is an authorized purge.
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        conn.execute(text("TRUNCATE TABLE audit_logs"))
        conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as(caller: _Caller, user_id: str) -> None:
    caller.user = _user(user_id)


def _audit(engine: Engine, schema: str) -> list[dict[str, Any]]:
    """Every audit row, whoever wrote it.

    Read under the purge GUC, the one setting that sees every actor's rows;
    without it the read would return the armed clinician's rows only and
    Blake's would be invisible (``audit_logs`` is FORCE RLS).
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT id::text, user_id::text, action, resource_id, "
                    "patient_id::text, changes::text AS changes FROM audit_logs ORDER BY id"
                )
            ).mappings()
        ]
        conn.rollback()
    return rows


def _unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _assert_carries_xavier(name: str, resp: Response, seed: _Seed) -> None:
    """The control's body is Xavier's document, so Blake's 404 is not vacuous.

    A PDF's text is compressed, so a PDF is held to its signature; the HTML,
    the JSON and the archive's ``patient.json`` are searched for his name.
    """
    if name in {"export_pdf", "superbill", "statement", "cms1500"}:
        assert resp.content.startswith(b"%PDF"), resp.content[:40]
    elif name == "export_zip":
        assert _X_LAST.encode() in _unzip(resp.content)["patient.json"]
    else:
        assert _X_LAST in resp.text
    if name == "intake_export":
        assert resp.headers["content-type"].startswith("text/html")
    del seed


def _assert_nothing_of_xaviers(resp: Response, seed: _Seed) -> None:
    for marker in (_X_LAST, _X_MEMBER, seed.x_control):
        assert marker not in resp.text
    assert not resp.content.startswith((b"%PDF", b"PK"))
    assert "attachment" not in resp.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# Routes that take Avery's client or claim by id
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Route:
    name: str
    path: Callable[[_Seed], str]


_ROUTES = (
    _Route("export_json", lambda s: f"/api/patients/{s.xavier}/export?format=json"),
    _Route("export_pdf", lambda s: f"/api/patients/{s.xavier}/export?format=pdf"),
    _Route("export_zip", lambda s: f"/api/patients/{s.xavier}/export?format=zip"),
    _Route(
        "intake_export",
        lambda s: f"/api/patients/{s.xavier}/intake-assignments/{s.x_form}/export",
    ),
    _Route(
        "superbill",
        lambda s: f"/api/patients/{s.xavier}/superbill?start={s.today}&end={s.today}",
    ),
    _Route("statement", lambda s: f"/api/patients/{s.xavier}/statement"),
    _Route("cms1500", lambda s: f"/api/claims/{s.x_claim}/cms1500.pdf"),
)


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blake_cannot_export_averys_client(  # noqa: PLR0913 — fixtures
    route: _Route,
    client: TestClient,
    caller: _Caller,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
) -> None:
    path = route.path(seed)

    # Control: the route hands the clinician who holds the grant the document.
    control = client.get(path)
    assert control.status_code == 200, (route.name, control.text[:500])
    _assert_carries_xavier(route.name, control, seed)

    before = _audit(engine, tenant_schema)
    # Not vacuous: Avery's export was audited, so a disclosure by Blake
    # would have shown up here too.
    assert any(r["user_id"] == _AVERY for r in before)

    _as(caller, _BLAKE)
    denied = client.get(path)

    assert denied.status_code == 404, (route.name, denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied, seed)
    # Nothing disclosed, so nothing audited: the trail is as Avery left it.
    assert _audit(engine, tenant_schema) == before


# ---------------------------------------------------------------------------
# Avery's form on Blake's own client's path
# ---------------------------------------------------------------------------


def test_blake_cannot_export_averys_form_under_their_own_client(
    client: TestClient, caller: _Caller, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    """The client half of the path is one Blake may see; the form half is not."""
    _as(caller, _BLAKE)
    # Control: Blake's own form on Yolanda's path is a document.
    own = client.get(f"/api/patients/{seed.yolanda}/intake-assignments/{seed.y_form}/export")
    assert own.status_code == 200, own.text[:500]
    assert _Y_LAST in own.text
    before = _audit(engine, tenant_schema)

    denied = client.get(f"/api/patients/{seed.yolanda}/intake-assignments/{seed.x_form}/export")

    assert denied.status_code == 404, (denied.status_code, denied.text[:500])
    _assert_nothing_of_xaviers(denied, seed)
    assert _audit(engine, tenant_schema) == before


# ---------------------------------------------------------------------------
# The claims CSV
# ---------------------------------------------------------------------------


def _csv(client: TestClient, seed: _Seed) -> Response:
    return client.get(f"/api/claims/export.csv?from={seed.today}&to={seed.today}")


def _csv_audit_claims(engine: Engine, schema: str, user_id: str) -> list[str]:
    [row] = [
        r
        for r in _audit(engine, schema)
        if r["user_id"] == user_id and r["action"] == "claims_exported"
    ]
    return list(json.loads(row["changes"])["claim_ids"])


def test_control_averys_csv_carries_xaviers_claim(
    client: TestClient, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    """The range below does select a claim, for someone holding its client's grant."""
    resp = _csv(client, seed)

    assert resp.status_code == 200, resp.text[:500]
    assert seed.x_control in resp.text
    assert _X_MEMBER in resp.text
    # And Avery's file carries nothing of Yolanda's.
    assert seed.y_control not in resp.text
    assert _Y_MEMBER not in resp.text
    assert _csv_audit_claims(engine, tenant_schema, _AVERY) == [seed.x_claim]


def test_blakes_csv_carries_none_of_averys_claims(
    client: TestClient, caller: _Caller, engine: Engine, tenant_schema: str, seed: _Seed
) -> None:
    _as(caller, _BLAKE)
    resp = _csv(client, seed)

    assert resp.status_code == 200, resp.text[:500]
    # Visibility control: Blake's own claim is in the file.
    assert seed.y_control in resp.text
    assert _Y_MEMBER in resp.text
    # None of Xavier's.
    assert seed.x_control not in resp.text
    assert _X_MEMBER not in resp.text
    assert _X_LAST not in resp.text
    # The disclosure Blake's export recorded names Yolanda's claim alone.
    assert _csv_audit_claims(engine, tenant_schema, _BLAKE) == [seed.y_claim]


# ---------------------------------------------------------------------------
# Ids that name nothing: Blake's 404 is a stranger's 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", _ROUTES, ids=lambda r: r.name)
def test_blakes_404_is_the_404_for_an_id_that_names_nothing(
    route: _Route, client: TestClient, caller: _Caller, seed: _Seed
) -> None:
    nobody = _Seed(
        xavier=str(uuid.uuid4()),
        x_claim=str(uuid.uuid4()),
        x_form=str(uuid.uuid4()),
        today=seed.today,
    )
    _as(caller, _BLAKE)

    theirs = client.get(route.path(seed))
    absent = client.get(route.path(nobody))

    assert (theirs.status_code, absent.status_code) == (404, 404)
    assert theirs.json() == _with_ids(absent.json(), nobody, seed)


def _with_ids(body: Any, nobody: _Seed, seed: _Seed) -> Any:
    """``body`` with the made-up ids swapped for the real ones.

    Some 404s name the id they were asked for; the request ids differ, and
    nothing else may.
    """
    raw = json.dumps(body)
    for made_up, real in (
        (nobody.xavier, seed.xavier),
        (nobody.x_claim, seed.x_claim),
        (nobody.x_form, seed.x_form),
    ):
        raw = raw.replace(made_up, real)
    return json.loads(raw)
