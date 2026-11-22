# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A route that checks the client in its path, then trusts an id in its body.

Two clinicians in one practice. Avery holds the ``patient_clinicians`` grant
on Xavier and owns every object below; Blake holds a grant on Yolanda and none
on Xavier. Each case is one route whose path names something Blake may reach
(Yolanda, or Blake's own appointment, note, thread, task or rule) while the
request body names an object of Avery's (a note, a session, an appointment, a
dictation, a document, a message, a task, an appointment type, a setup intent,
an intake form) or Xavier himself.

The route checks the path. The question each case asks is whether it also
checks the body, or lets Blake link Avery's object onto Yolanda's chart, or
Blake's object onto Xavier.

Every request runs through the real app, the real repositories and the real
``has_patient_access``, against a practice schema provisioned the production
way, on a connection subject to row security, with the principal armed as
whoever is calling. Each case:

* **Control.** Avery makes the same request about Xavier, naming Avery's own
  object, and gets a 2xx. The request shape works; Blake's refusal is not a
  validation error that would refuse anyone.
* **Blake's request.** The same route, Blake's own path, Avery's id in the
  body. Blake gets a refusal (404, or 422 / 400 where the route answers an
  unknown id that way), and exactly the answer a made-up id gets, so the
  status says nothing about whether Avery's object exists.
* **Nothing written.** Every row in the practice schema that mentions
  Xavier or Avery's object, read as Avery and as Blake (and the audit trail
  under the purge setting that sees every actor's rows), is the same after
  Blake's request as before it.

One route ignores the body id by design: the transcript upload files the
session under the path's client. There the case expects the upload to go
through and asserts the same footprint: nothing lands on Xavier.

A case whose refusal is missing today is marked ``xfail(strict=True)`` with
the bug that tracks it, so the fix turns it green and the marker must go.

Run: ``make test-integration``.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from httpx import Response
    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")

_TENANT_EMAIL = "e2e-cross-clinician-body@example.com"
_AVERY = "a7e1c0de-0000-4000-8000-0000000000a3"
_BLAKE = "b1a4e0de-0000-4000-8000-0000000000b4"
_NOW = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
# Each seeded appointment gets its own hour: a clinician's live bookings may
# not overlap, and the practice outlives a single test.
_BOOKED_HOURS = itertools.count(1)

# A diagnostic definition made up for the test: the assessment route needs
# one, and what it says clinically is beside the point here.
_DIAGNOSTIC_DEFINITION: dict[str, Any] = {
    "code": "cross_clinician_probe",
    "version": 1,
    "display_name": "Probe",
    "evaluator_type": "criteria",
    "suggested_icd10": "T00.1",
    "params": {
        "criterion_groups": [
            {
                "key": "A",
                "label": "Group A",
                "min_met": 1,
                "require_cardinal": False,
                "criteria": [{"key": "A1", "label": "Alpha"}],
            }
        ],
        "gates": [{"key": "g1", "label": "Gate one"}],
        "icd10_options": [{"code": "T00.1", "label": "Probe code"}],
    },
}


@dataclass
class _Chart:
    """One clinician's client and the objects on it, all theirs."""

    clinician: str
    patient: str = field(default_factory=lambda: str(uuid.uuid4()))
    note: str = field(default_factory=lambda: str(uuid.uuid4()))
    session: str = field(default_factory=lambda: str(uuid.uuid4()))
    card_customer: str = ""

    def __post_init__(self) -> None:
        self.card_customer = f"cus_{self.clinician[:8]}"


@dataclass
class _Seed:
    avery: _Chart = field(default_factory=lambda: _Chart(_AVERY))
    blake: _Chart = field(default_factory=lambda: _Chart(_BLAKE))


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


def _armed(conn: Connection, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})


def _insert(engine: Engine, schema: str, who: str, sql: str, **params: Any) -> None:
    """One row written as ``who``, so it passes the policy a request would."""
    with engine.begin() as conn:
        _armed(conn, schema, who)
        conn.execute(text(sql), {"u": who, "now": _NOW, **params})


@pytest.fixture(scope="module")
def seed(engine: Engine, tenant_schema: str) -> _Seed:
    """Each clinician's client, with a grant, a signed note, a session and a card."""
    s = _Seed()
    for chart, first in ((s.avery, "Xavier"), (s.blake, "Yolanda")):
        _insert(
            engine,
            tenant_schema,
            chart.clinician,
            "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
            "last_name_lower, email, status, session_count, created_at, updated_at) "
            "VALUES (CAST(:pid AS uuid), :fn, 'Client', :fnl, 'client', :email, "
            "'active', 0, :now, :now)",
            pid=chart.patient,
            fn=first,
            fnl=first.lower(),
            email=f"{first.lower()}@example.com",
        )
        _insert(
            engine,
            tenant_schema,
            chart.clinician,
            "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
            "VALUES (CAST(:pid AS uuid), :u, :u)",
            pid=chart.patient,
        )
        _insert(
            engine,
            tenant_schema,
            chart.clinician,
            "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
            "session_number, status, transcript, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), :now, 1, "
            "'finalized', CAST('{}' AS jsonb), :now)",
            id=chart.session,
            pid=chart.patient,
        )
        _insert(
            engine,
            tenant_schema,
            chart.clinician,
            "INSERT INTO notes (id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at, finalized_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, 'soap', "
            "CAST(:c AS jsonb), 'complete', CAST(:u AS uuid), false, :now, :now, :now)",
            id=chart.note,
            pid=chart.patient,
            c=json.dumps({"subjective": "s", "objective": "", "assessment": "", "plan": ""}),
        )
        _insert(
            engine,
            tenant_schema,
            chart.clinician,
            "INSERT INTO patient_payment_methods (id, patient_id, stripe_customer_id, "
            "stripe_payment_method_id, card_brand, card_last4, card_exp_month, "
            "card_exp_year, created_by_user_id, created_at) "
            "VALUES (:id, CAST(:pid AS uuid), :cus, :pm, 'visa', '4242', 12, 2030, :u, :now)",
            id=uuid.uuid4().hex,
            pid=chart.patient,
            cus=chart.card_customer,
            pm=f"pm_{chart.clinician[:8]}",
        )
    return s


# ---------------------------------------------------------------------------
# Objects made per case, so no case meets another's leftovers
# ---------------------------------------------------------------------------


@dataclass
class _World:
    client: TestClient
    caller: _Caller
    engine: Engine
    schema: str
    seed: _Seed
    stripe: _FakeStripe

    def as_avery(self) -> None:
        self.caller.user = _user(_AVERY, "Avery")

    def as_blake(self) -> None:
        self.caller.user = _user(_BLAKE, "Blake")

    def chart(self, who: str) -> _Chart:
        return self.seed.avery if who == _AVERY else self.seed.blake

    def appointment(self, who: str, type_id: str | None = None) -> str:
        new = str(uuid.uuid4())
        start = _NOW + timedelta(days=1, hours=next(_BOOKED_HOURS))
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
            "duration_minutes, status, session_type, is_exception, reminder_24h_sent, "
            "reminder_1h_sent, appointment_type_id, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:pid AS uuid), 'Session', "
            ":start, :end, 50, 'confirmed', 'individual', false, false, false, "
            "CAST(:t AS uuid), :now)",
            id=new,
            pid=self.chart(who).patient,
            start=start,
            end=start + timedelta(minutes=50),
            t=type_id,
        )
        return new

    def appointment_type(self, who: str) -> str:
        new = str(uuid.uuid4())
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO appointment_types (id, user_id, name, duration_minutes, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:u AS uuid), :name, 50, :now)",
            id=new,
            name=f"Type {new[:8]}",
        )
        return new

    def availability_rule(self, who: str) -> str:
        new = str(uuid.uuid4())
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO availability_rules (id, user_id, rule_type, enforcement, params, "
            "created_at) VALUES (CAST(:id AS uuid), CAST(:u AS uuid), 'working_hours', "
            "'hard', CAST(:p AS jsonb), :now)",
            id=new,
            p=json.dumps({"day_of_week": 1, "start": "09:00", "end": "17:00"}),
        )
        return new

    def dictation(self, who: str) -> str:
        """A transcribed dictation offering a draft addendum to ``who``'s note."""
        new = str(uuid.uuid4())
        chart = self.chart(who)
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO session_dictations (id, session_id, note_id, patient_id, "
            "author_user_id, audio_path, content_type, status, transcript, used_as, "
            "created_at) VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:nid AS uuid), "
            "CAST(:pid AS uuid), CAST(:u AS uuid), :path, 'audio/webm', 'transcribed', "
            "'One more thing from the session.', 'addendum', :now)",
            id=new,
            sid=chart.session,
            nid=chart.note,
            pid=chart.patient,
            path=f"dictations/{chart.session}/{new}",
        )
        return new

    def thread(self, who: str) -> str:
        new = str(uuid.uuid4())
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO patient_message_threads (id, patient_id, status, created_at, "
            "last_message_at) VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'open', "
            ":now, :now)",
            id=new,
            pid=self.chart(who).patient,
        )
        return new

    def client_message(self, who: str, thread_id: str) -> str:
        new = str(uuid.uuid4())
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, "
            "created_at) VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:pid AS uuid), "
            "'patient', 'A question for you', :now)",
            id=new,
            t=thread_id,
            pid=self.chart(who).patient,
        )
        return new

    def document(self, who: str) -> str:
        """A finished message attachment ``who`` uploaded for their own client."""
        new = str(uuid.uuid4())
        chart = self.chart(who)
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO patient_documents (id, patient_id, user_id, filename, mime_type, "
            "gcs_path, size_bytes, category, created_at, finalized_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:u AS uuid), "
            "'handout.pdf', 'application/pdf', :gcs, 1, 'message', :now, :now)",
            id=new,
            pid=chart.patient,
            gcs=f"patients/{chart.patient}/{new}.pdf",
        )
        return new

    def task(self, who: str) -> str:
        new = str(uuid.uuid4())
        _insert(
            self.engine,
            self.schema,
            who,
            "INSERT INTO tasks (id, user_id, item_type, label, category, created_at, "
            "updated_at) VALUES (CAST(:id AS uuid), CAST(:u AS uuid), 'task', "
            "'Call back', 'practice', :now, :now)",
            id=new,
        )
        return new

    def intake_form(self, who: str) -> tuple[str, str]:
        """A submitted intake form on ``who``'s client: (assignment, question)."""
        ids = {name: str(uuid.uuid4()) for name in ("template", "version", "item", "asg")}
        tables = ("intake_packet_templates", "intake_packet_versions", "intake_item_definitions")
        chart = self.chart(who)
        with self.engine.begin() as conn:
            _armed(conn, self.schema, who)
            # Practice-level form rows are written by provisioning, not by a
            # clinician; lift FORCE for the three inserts, as the purge test does.
            for table in tables:
                conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))
            conn.execute(
                text(
                    "INSERT INTO intake_packet_templates (id, name, created_at) "
                    "VALUES (CAST(:id AS uuid), 'New client', now())"
                ),
                {"id": ids["template"]},
            )
            conn.execute(
                text(
                    "INSERT INTO intake_packet_versions (id, template_id, version, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 1, now())"
                ),
                {"id": ids["version"], "t": ids["template"]},
            )
            conn.execute(
                text(
                    "INSERT INTO intake_item_definitions (id, version_id, key, position, "
                    "item_type) VALUES (CAST(:id AS uuid), CAST(:v AS uuid), 'reason', 0, "
                    "'free_text')"
                ),
                {"id": ids["item"], "v": ids["version"]},
            )
            for table in tables:
                conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            conn.execute(
                text(
                    "INSERT INTO patient_intake_assignments (id, patient_id, version_id, "
                    "status, assigned_at, updated_at) VALUES (CAST(:a AS uuid), "
                    "CAST(:pid AS uuid), CAST(:v AS uuid), 'submitted', now(), now())"
                ),
                {"a": ids["asg"], "pid": chart.patient, "v": ids["version"]},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_intake_responses (id, assignment_id, patient_id, "
                    "item_id, value, created_at, updated_at) VALUES (gen_random_uuid(), "
                    "CAST(:a AS uuid), CAST(:pid AS uuid), CAST(:i AS uuid), "
                    'CAST(\'{"text": "an answer"}\' AS jsonb), now(), now())'
                ),
                {"a": ids["asg"], "pid": chart.patient, "i": ids["item"]},
            )
        return ids["asg"], ids["item"]

    def via_api(self, who: str, method: str, path: str, body: dict[str, Any]) -> str:
        """Create an object through its own route, as ``who``; return its id."""
        (self.as_avery if who == _AVERY else self.as_blake)()
        resp = self.client.request(method, path, json=body)
        assert resp.status_code in (200, 201), resp.text
        return str(resp.json()["id"])


class _FakeStripe:
    """The two Stripe calls the card routes make, answered from a table."""

    def __init__(self) -> None:
        # setup intent id -> the customer it was created for
        self.setup_intents: dict[str, str] = {}

    def stripe_request(self, method: str, path: str, **_: Any) -> dict[str, Any]:
        if path.startswith("/v1/setup_intents/"):
            customer = self.setup_intents.get(path.rsplit("/", 1)[-1])
            if customer is None:
                return {"customer": None, "status": "requires_payment_method"}
            return {"customer": customer, "status": "succeeded", "payment_method": "pm_new"}
        if path.startswith("/v1/payment_methods/"):
            return {"card": {"brand": "visa", "last4": "1881", "exp_month": 1, "exp_year": 2031}}
        raise AssertionError(f"unexpected Stripe call {method} {path}")

    @staticmethod
    def payment_intent_request(path: str, **_: Any) -> tuple[bool, dict[str, Any]]:
        if path.endswith("/confirm"):
            return True, {"id": path.split("/")[3], "status": "succeeded"}
        return True, {"id": f"pi_{uuid.uuid4().hex[:16]}"}


# ---------------------------------------------------------------------------
# Per-test client: one app, the caller switched per request
# ---------------------------------------------------------------------------


def _user(user_id: str, name: str) -> User:
    from app.models import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=_TENANT_EMAIL,
        name=name,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def world(
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    seed: _Seed,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_World]:
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
    from app.diagnostics.definition_provider import BaselineDefinitionProvider  # noqa: PLC0415
    from app.diagnostics.router import get_diagnostic_definition_provider  # noqa: PLC0415
    from app.payments.provider import PaymentCredentials  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    caller = _Caller(user=_user(_AVERY, "Avery"))

    def _stash_identity(request: Request) -> None:
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
        # Arm the CALLER: a policy evaluated as the wrong clinician would pass
        # or fail for the wrong reason.
        arm_current_user_id(get_db_session(), caller.user.id)
        return TenantContext(
            user_id=caller.user.id,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    def _user_now() -> User:
        # The real user dependencies arm the request's principal before the
        # route body runs; a route whose only auth dependency is this one
        # would otherwise run unarmed and refuse its own clinician.
        arm_current_user_id(get_db_session(), caller.user.id)
        return caller.user

    class _Credentials:
        @staticmethod
        def credentials_for_practice(_practice_id: str | None) -> PaymentCredentials:
            return PaymentCredentials(secret_key="sk_test_probe")  # noqa: S106 — a fake

    stripe = _FakeStripe()
    monkeypatch.setattr("app.routes.patient_payments.get_payment_credential_provider", _Credentials)
    monkeypatch.setattr("app.routes.patient_payments.stripe_request", stripe.stripe_request)
    monkeypatch.setattr(
        "app.routes.patient_payments.payment_intent_request", stripe.payment_intent_request
    )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: caller.user.id
    fastapi_app.dependency_overrides[get_current_user] = _user_now
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = _user_now
    fastapi_app.dependency_overrides[require_active_subscription] = _user_now
    fastapi_app.dependency_overrides[require_baa_acceptance] = _user_now
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context
    fastapi_app.dependency_overrides[get_diagnostic_definition_provider] = lambda: (
        BaselineDefinitionProvider([_DIAGNOSTIC_DEFINITION])
    )

    try:
        yield _World(
            # A 500 is an answer to compare, not a test crash.
            client=TestClient(fastapi_app, raise_server_exceptions=False),
            caller=caller,
            engine=engine,
            schema=tenant_schema,
            seed=seed,
            stripe=stripe,
        )
    finally:
        fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# The footprint: every row that mentions Xavier or the object Blake named
# ---------------------------------------------------------------------------


def _footprint(w: _World, markers: list[str]) -> list[tuple[str, str]]:
    """Every row in the practice schema whose text carries one of ``markers``.

    Read as Avery, then as Blake: between them they see every row either
    clinician's request could have written (Avery's client's rows and Avery's
    own; Blake's client's rows and Blake's own). The audit trail is read under
    the purge setting, the one that sees every actor's rows.
    """
    with w.engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_type = 'BASE TABLE' ORDER BY 1"
                ),
                {"s": w.schema},
            )
        ]
    pattern = "|".join(markers)
    seen: set[tuple[str, str]] = set()
    for who in (_AVERY, _BLAKE):
        with w.engine.connect() as conn:
            _armed(conn, w.schema, who)
            conn.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
            for table in tables:
                # Schema and table names come from information_schema above.
                query = f'SELECT t::text FROM "{w.schema}"."{table}" t WHERE t::text ~ :p'  # noqa: S608
                rows = conn.execute(
                    text(query),
                    {"p": pattern},
                ).scalars()
                seen.update((table, row) for row in rows)
            conn.rollback()
    return sorted(seen)


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Request:
    method: str
    path: str
    body: dict[str, Any]


# Every case below that is marked with this bug stores the body's id today.
_STORED_UNCHECKED = "PABLO-3tho"

# Built per case: (control request as Avery, Blake's request builder taking the
# foreign id, the foreign id, extra markers for the footprint).
_Built = tuple[_Request, Callable[[str], _Request], str, list[str]]


@dataclass(frozen=True)
class _Case:
    name: str
    route: str  # METHOD path, as the sweep lists it
    build: Callable[[_World], _Built]
    refusals: tuple[int, ...] = (404, 422)
    xfail: str | None = None


def _problem_source_note(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    label = f"Insomnia {uuid.uuid4().hex[:6]}"

    def blake(foreign: str) -> _Request:
        return _Request(
            "POST",
            f"/api/patients/{b.patient}/problems",
            {"label": f"Insomnia {uuid.uuid4().hex[:6]}", "source_note_id": foreign},
        )

    ctl = _Request(
        "POST", f"/api/patients/{a.patient}/problems", {"label": label, "source_note_id": a.note}
    )
    return ctl, blake, a.note, []


def _problem_assessment(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    assessment = w.via_api(
        _AVERY,
        "POST",
        f"/api/patients/{a.patient}/diagnostic-assessments",
        _assessment_body(),
    )

    def blake(foreign: str) -> _Request:
        return _Request(
            "POST",
            f"/api/patients/{b.patient}/problems",
            {"label": f"Probe {uuid.uuid4().hex[:6]}", "diagnostic_assessment_id": foreign},
        )

    ctl = _Request(
        "POST",
        f"/api/patients/{a.patient}/problems",
        {"label": f"Probe {uuid.uuid4().hex[:6]}", "diagnostic_assessment_id": assessment},
    )
    return ctl, blake, assessment, []


def _problem_order(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    problem = w.via_api(
        _AVERY,
        "POST",
        f"/api/patients/{a.patient}/problems",
        {"label": f"Order {uuid.uuid4().hex[:6]}"},
    )

    def live(patient: str) -> list[str]:
        resp = w.client.get(f"/api/patients/{patient}/problems")
        assert resp.status_code == 200, resp.text
        return [p["id"] for p in resp.json()["data"]]

    w.as_avery()
    avery_list = live(a.patient)
    w.as_blake()
    blake_list = live(b.patient)

    def blake(foreign: str) -> _Request:
        return _Request(
            "PUT",
            f"/api/patients/{b.patient}/problems/order",
            {"problem_ids": [*blake_list, foreign]},
        )

    ctl = _Request(
        "PUT", f"/api/patients/{a.patient}/problems/order", {"problem_ids": avery_list[::-1]}
    )
    return ctl, blake, problem, []


def _chart_history_source_note(w: _World) -> _Built:
    from app.chart_history.fields import HISTORY_KEYS  # noqa: PLC0415

    a, b = w.seed.avery, w.seed.blake
    key = HISTORY_KEYS[0]

    def blake(foreign: str) -> _Request:
        return _Request(
            "PUT",
            f"/api/patients/{b.patient}/chart-history/{key}",
            {"text": f"From a note {uuid.uuid4().hex[:6]}", "source_note_id": foreign},
        )

    ctl = _Request(
        "PUT",
        f"/api/patients/{a.patient}/chart-history/{key}",
        {"text": f"From a note {uuid.uuid4().hex[:6]}", "source_note_id": a.note},
    )
    return ctl, blake, a.note, []


def _allergies_source_note(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake

    def body(note: str) -> dict[str, Any]:
        return {
            "status": "recorded",
            "allergies": [{"substance": "Penicillin", "source_note_id": note}],
        }

    ctl = _Request("PUT", f"/api/patients/{a.patient}/allergies", body(a.note))
    return (
        ctl,
        lambda foreign: _Request("PUT", f"/api/patients/{b.patient}/allergies", body(foreign)),
        a.note,
        [],
    )


def _note_appointment(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    control_appt = w.appointment(_AVERY)
    target = w.appointment(_AVERY)

    def body(appt: str) -> dict[str, Any]:
        return {"note_type": "soap", "appointment_id": appt}

    ctl = _Request("POST", f"/api/patients/{a.patient}/notes", body(control_appt))
    return (
        ctl,
        lambda foreign: _Request("POST", f"/api/patients/{b.patient}/notes", body(foreign)),
        target,
        [],
    )


def _addendum_dictation(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    # Two drafts: the control signs one, and Blake names the other, which is
    # still waiting, so a refusal is not just "already used".
    control_draft = w.dictation(_AVERY)
    target = w.dictation(_AVERY)

    def body(draft: str, signer: str) -> dict[str, Any]:
        return {
            "text": "One more thing from the session.",
            "signer_name": signer,
            "dictation_id": draft,
        }

    ctl = _Request("POST", f"/api/notes/{a.note}/addenda", body(control_draft, "Avery"))
    return (
        ctl,
        lambda foreign: _Request("POST", f"/api/notes/{b.note}/addenda", body(foreign, "Blake")),
        target,
        [],
    )


def _appointment_patient(w: _World) -> _Built:
    a = w.seed.avery
    control_appt = w.appointment(_AVERY)
    blake_appt = w.appointment(_BLAKE)
    ctl = _Request("PATCH", f"/api/appointments/{control_appt}", {"patient_id": a.patient})
    return (
        ctl,
        lambda foreign: _Request(
            "PATCH", f"/api/appointments/{blake_appt}", {"patient_id": foreign}
        ),
        a.patient,
        [],
    )


def _appointment_type(w: _World) -> _Built:
    avery_type = w.appointment_type(_AVERY)
    control_appt = w.appointment(_AVERY)
    blake_appt = w.appointment(_BLAKE)
    ctl = _Request(
        "PATCH", f"/api/appointments/{control_appt}", {"appointment_type_id": avery_type}
    )
    return (
        ctl,
        lambda foreign: _Request(
            "PATCH", f"/api/appointments/{blake_appt}", {"appointment_type_id": foreign}
        ),
        avery_type,
        [],
    )


def _rule_type(w: _World) -> _Built:
    avery_type = w.appointment_type(_AVERY)
    control_rule = w.availability_rule(_AVERY)
    blake_rule = w.availability_rule(_BLAKE)
    ctl = _Request(
        "PATCH", f"/api/availability/rules/{control_rule}", {"appointment_type_id": avery_type}
    )
    return (
        ctl,
        lambda foreign: _Request(
            "PATCH", f"/api/availability/rules/{blake_rule}", {"appointment_type_id": foreign}
        ),
        avery_type,
        [],
    )


def _session_upload(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake

    def body(patient: str) -> dict[str, Any]:
        return {
            "patient_id": patient,
            "session_date": _NOW.isoformat(),
            "transcript": {"format": "txt", "content": "Therapist: How was the week?"},
        }

    ctl = _Request("POST", f"/api/patients/{a.patient}/sessions/upload", body(a.patient))
    return (
        ctl,
        lambda foreign: _Request(
            "POST", f"/api/patients/{b.patient}/sessions/upload", body(foreign)
        ),
        a.patient,
        [],
    )


def _reply_attachment(w: _World) -> _Built:
    avery_thread = w.thread(_AVERY)
    blake_thread = w.thread(_BLAKE)
    control_doc = w.document(_AVERY)
    target = w.document(_AVERY)

    def body(doc: str) -> dict[str, Any]:
        return {"body": "Here is the handout.", "attachment_ids": [doc]}

    ctl = _Request("POST", f"/api/message-threads/{avery_thread}/replies", body(control_doc))
    return (
        ctl,
        lambda foreign: _Request(
            "POST", f"/api/message-threads/{blake_thread}/replies", body(foreign)
        ),
        target,
        [],
    )


def _reply_in_reply_to(w: _World) -> _Built:
    avery_thread = w.thread(_AVERY)
    control_msg = w.client_message(_AVERY, avery_thread)
    target = w.client_message(_AVERY, avery_thread)
    blake_thread = w.thread(_BLAKE)
    w.client_message(_BLAKE, blake_thread)

    def body(msg: str) -> dict[str, Any]:
        return {"body": "Thanks, noted.", "in_reply_to_message_id": msg}

    ctl = _Request("POST", f"/api/message-threads/{avery_thread}/replies", body(control_msg))
    return (
        ctl,
        lambda foreign: _Request(
            "POST", f"/api/message-threads/{blake_thread}/replies", body(foreign)
        ),
        target,
        [],
    )


def _charge_appointment(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    control_appt = w.appointment(_AVERY)
    target = w.appointment(_AVERY)

    def body(appt: str) -> dict[str, Any]:
        return {"kind": "session", "amount_cents": 15000, "appointment_id": appt}

    ctl = _Request("POST", f"/api/patients/{a.patient}/charges", body(control_appt))
    return (
        ctl,
        lambda foreign: _Request("POST", f"/api/patients/{b.patient}/charges", body(foreign)),
        target,
        [],
    )


def _payment_method_setup_intent(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    intent = f"seti_{uuid.uuid4().hex[:12]}"
    w.stripe.setup_intents[intent] = a.card_customer
    ctl = _Request("POST", f"/api/patients/{a.patient}/payment-method", {"setup_intent_id": intent})
    return (
        ctl,
        lambda foreign: _Request(
            "POST", f"/api/patients/{b.patient}/payment-method", {"setup_intent_id": foreign}
        ),
        intent,
        # The card a confirmed setup stores; on Yolanda's row it would mean
        # Avery's client's card now pays for Blake's client.
        ["pm_new"],
    )


def _task_body(**extra: Any) -> dict[str, Any]:
    return {"label": "Call back", "category": "practice", **extra}


def _task_patient(w: _World) -> _Built:
    a = w.seed.avery
    control_task = w.task(_AVERY)
    blake_task = w.task(_BLAKE)
    ctl = _Request("PUT", f"/api/tasks/{control_task}", _task_body(patient_id=a.patient))
    return (
        ctl,
        lambda foreign: _Request("PUT", f"/api/tasks/{blake_task}", _task_body(patient_id=foreign)),
        a.patient,
        [],
    )


def _task_parent(w: _World) -> _Built:
    parent = w.task(_AVERY)
    control_task = w.task(_AVERY)
    blake_task = w.task(_BLAKE)
    ctl = _Request("PUT", f"/api/tasks/{control_task}", _task_body(parent_task_id=parent))
    return (
        ctl,
        lambda foreign: _Request(
            "PUT", f"/api/tasks/{blake_task}", _task_body(parent_task_id=foreign)
        ),
        parent,
        [],
    )


def _measure_body(**link: str) -> dict[str, Any]:
    return {
        "instrument": "phq9",
        "source": "manual",
        "administered_at": _NOW.isoformat(),
        "total_score": 7,
        **link,
    }


def _assessment_body(**link: str) -> dict[str, Any]:
    return {
        "instrument": _DIAGNOSTIC_DEFINITION["code"],
        "source": "manual",
        "assessed_at": _NOW.isoformat(),
        "criterion_responses": {"A1": True},
        "gate_responses": {"g1": True},
        "determined_icd10": "T00.1",
        **link,
    }


def _linked_record(
    route: str, body: Callable[..., dict[str, Any]], link: str
) -> Callable[[_World], _Built]:
    """An outcome measure or assessment on the caller's client, tied to a visit."""

    def build(w: _World) -> _Built:
        a, b = w.seed.avery, w.seed.blake
        if link == "session_id":
            control_target, target = a.session, a.session
        else:
            control_target, target = w.appointment(_AVERY), w.appointment(_AVERY)
        ctl = _Request("POST", f"/api/patients/{a.patient}/{route}", body(**{link: control_target}))
        return (
            ctl,
            lambda foreign: _Request(
                "POST", f"/api/patients/{b.patient}/{route}", body(**{link: foreign})
            ),
            target,
            [],
        )

    return build


def _intake_correction(w: _World) -> _Built:
    a, b = w.seed.avery, w.seed.blake
    control_form, control_item = w.intake_form(_AVERY)
    target, item = w.intake_form(_AVERY)
    ctl = _Request(
        "POST",
        f"/api/patients/{a.patient}/intake-assignments/{control_form}/request-correction",
        {"item_ids": [control_item], "note": "Could you say a little more?"},
    )
    return (
        ctl,
        lambda foreign: _Request(
            "POST",
            f"/api/patients/{b.patient}/intake-assignments/{foreign}/request-correction",
            {"item_ids": [item], "note": "Could you say a little more?"},
        ),
        target,
        [item],
    )


_CASES = (
    _Case(
        "problem_source_note",
        "POST /api/patients/{patient_id}/problems",
        _problem_source_note,
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "problem_diagnostic_assessment",
        "POST /api/patients/{patient_id}/problems",
        _problem_assessment,
    ),
    # Any list that is not exactly the client's live problems is a 400, the
    # same for an unknown id as for a colleague's.
    _Case(
        "problem_order",
        "PUT /api/patients/{patient_id}/problems/order",
        _problem_order,
        refusals=(400,),
    ),
    _Case(
        "chart_history_source_note",
        "PUT /api/patients/{patient_id}/chart-history/{key}",
        _chart_history_source_note,
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "allergies_source_note",
        "PUT /api/patients/{patient_id}/allergies",
        _allergies_source_note,
        xfail=_STORED_UNCHECKED,
    ),
    _Case("note_appointment", "POST /api/patients/{patient_id}/notes", _note_appointment),
    _Case("addendum_dictation", "POST /api/notes/{note_id}/addenda", _addendum_dictation),
    _Case(
        "appointment_patient",
        "PATCH /api/appointments/{appointment_id}",
        _appointment_patient,
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "appointment_type",
        "PATCH /api/appointments/{appointment_id}",
        _appointment_type,
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "availability_rule_type",
        "PATCH /api/availability/rules/{rule_id}",
        _rule_type,
        xfail=_STORED_UNCHECKED,
    ),
    # The body's patient_id is ignored: the session is filed under the path's
    # client. Blake's upload goes through, onto Yolanda, and nothing of it
    # reaches Xavier.
    _Case(
        "session_upload_patient",
        "POST /api/patients/{patient_id}/sessions/upload",
        _session_upload,
        refusals=(202,),
    ),
    _Case("reply_attachment", "POST /api/message-threads/{thread_id}/replies", _reply_attachment),
    _Case("reply_in_reply_to", "POST /api/message-threads/{thread_id}/replies", _reply_in_reply_to),
    _Case(
        "charge_appointment",
        "POST /api/patients/{patient_id}/charges",
        _charge_appointment,
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "payment_method_setup_intent",
        "POST /api/patients/{patient_id}/payment-method",
        _payment_method_setup_intent,
    ),
    _Case("task_patient", "PUT /api/tasks/{task_id}", _task_patient),
    _Case("task_parent", "PUT /api/tasks/{task_id}", _task_parent),
    _Case(
        "outcome_measure_session",
        "POST /api/patients/{patient_id}/outcome-measures",
        _linked_record("outcome-measures", _measure_body, "session_id"),
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "outcome_measure_appointment",
        "POST /api/patients/{patient_id}/outcome-measures",
        _linked_record("outcome-measures", _measure_body, "appointment_id"),
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "diagnostic_assessment_session",
        "POST /api/patients/{patient_id}/diagnostic-assessments",
        _linked_record("diagnostic-assessments", _assessment_body, "session_id"),
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "diagnostic_assessment_appointment",
        "POST /api/patients/{patient_id}/diagnostic-assessments",
        _linked_record("diagnostic-assessments", _assessment_body, "appointment_id"),
        xfail=_STORED_UNCHECKED,
    ),
    _Case(
        "intake_correction",
        "POST /api/patients/{patient_id}/intake-assignments/{assignment_id}/request-correction",
        _intake_correction,
    ),
)


def _call(w: _World, req: _Request) -> Response:
    return w.client.request(req.method, req.path, json=req.body)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=c.xfail)) if c.xfail else c
        for c in _CASES
    ],
    ids=lambda c: c.name,
)
def test_body_cannot_name_another_clinicians_object(case: _Case, world: _World) -> None:
    w = world
    ctl, blake, foreign, extra = case.build(w)
    markers = [w.seed.avery.patient, foreign, *extra]

    # Control: the same request shape works for the clinician who owns both
    # sides of it.
    w.as_avery()
    control = _call(w, ctl)
    assert 200 <= control.status_code < 300, (case.name, control.status_code, control.text)

    before = _footprint(w, markers)
    # Not vacuous: the footprint sees the object Blake is about to name.
    assert any(m in row for _, row in before for m in (foreign, *extra)), case.name

    w.as_blake()
    denied = _call(w, blake(foreign))
    after = _footprint(w, markers)

    # Nothing of Avery's moved, and nothing of Blake's now points at it.
    assert after == before, (case.name, denied.status_code, sorted(set(after) ^ set(before)))
    assert denied.status_code in case.refusals, (case.name, denied.status_code, denied.text)

    # The refusal says nothing about whether Avery's object exists: a made-up
    # id gets the same answer.
    made_up = _call(w, blake(str(uuid.uuid4())))
    assert denied.status_code == made_up.status_code, (case.name, made_up.text)


# ---------------------------------------------------------------------------
# The sweep: every route of this shape has a case, or a reason it has none
# ---------------------------------------------------------------------------

# Routes whose path names an object and whose body names another by id, but
# where the body's id is not another clinician's object.
_NOT_A_CLINICIANS_OBJECT = {
    # Payers are the practice's list, shared by every clinician; member_id is
    # free text from the client's card.
    "PATCH /api/payers/{payer_row_id}": "payer_id: practice-wide payer list",
    "POST /api/patients/{patient_id}/coverage": "payer_id: practice list; member_id: free text",
    "PATCH /api/patients/{patient_id}/coverage": "payer_id: practice list; member_id: free text",
    # Naming a colleague here is the point of the route: who covers, who is
    # assigned. The id is a person, not an object of theirs.
    "POST /api/message-threads/{thread_id}/assign": "user_id: the colleague assigned",
    "PATCH /api/away-periods/{period_id}": "covering_user_id: the colleague covering",
    "POST /api/patients/{patient_id}/payment-method": "covered by payment_method_setup_intent",
    # A practice intake form version, shared by every clinician.
    "POST /api/patients/{patient_id}/intake-assignments": "version_id: practice form",
    "POST /api/patients/{patient_id}/portal-invite/preview": "version_ids: practice forms",
    # The caller is a client in the portal, not a clinician.
    "POST /api/patient/messages/threads/{thread_id}/messages": "portal caller",
    "POST /api/patient/intake/assignments/{assignment_id}/signatures": "portal caller",
    "POST /api/patient/intake/assignments/{assignment_id}/artifacts": "portal caller",
    "PUT /api/patient/intake/assignments/{assignment_id}/items/{item_id}/coverage": (
        "portal caller"
    ),
    # Checks the type is the caller's own already (``_require_own_type``).
    "PATCH /api/booking-links/{link_id}": "appointment_type_id: checked as the caller's own",
}


def _body_id_fields(model: type[Any], depth: int = 0) -> list[str]:
    from pydantic import BaseModel  # noqa: PLC0415

    found: list[str] = []
    for name, info in model.model_fields.items():
        if name.endswith(("_id", "_ids")):
            found.append(name)
        if depth < 3:
            stack: list[Any] = [info.annotation]
            while stack:
                ann = stack.pop()
                if isinstance(ann, type) and issubclass(ann, BaseModel):
                    found += _body_id_fields(ann, depth + 1)
                stack += list(getattr(ann, "__args__", ()))
    return found


def test_every_route_of_this_shape_has_a_case(fastapi_app: FastAPI) -> None:
    """A route whose path names an object and whose body names another, by id."""
    from app.route_introspection import iter_api_routes  # noqa: PLC0415
    from pydantic import BaseModel  # noqa: PLC0415

    swept: set[str] = set()
    for path, route in iter_api_routes(fastapi_app):
        if not any(p.name.endswith("_id") for p in route.dependant.path_params):
            continue
        for param in route.dependant.body_params:
            model = param.field_info.annotation
            if isinstance(model, type) and issubclass(model, BaseModel) and _body_id_fields(model):
                swept.update(f"{method} {path}" for method in route.methods)

    cased = {c.route for c in _CASES}
    assert swept - cased - set(_NOT_A_CLINICIANS_OBJECT) == set(), (
        "a route of this shape has no case here"
    )
    # Only the cased routes must still exist: some exclusions are mounted
    # behind a deployment flag (the client portal) that may be off here.
    assert cased - swept == set(), "a case names a route that is gone"
