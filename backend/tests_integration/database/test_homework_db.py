# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for homework: suggestions beside the note, and who sees what.

Against a schema built by the real ``create_practice_schema`` (the template
and policies that ship), connected as the ``NOSUPERUSER NOBYPASSRLS`` role
the integration conftest creates.

What only a database can prove, and what each test would catch:

* a note's homework is stored as its chart proposals of the ``homework``
  family, brought up to date when the note is saved again, and reachable
  only through the clinician's grant;
* accepting a suggestion through the chart-proposal service is what makes the
  assignment, with the clinician's wording when they edited it, and a
  decided suggestion is not offered again;
* a client sees only open and done assignments, and no suggestion, through
  the row policies themselves, and cannot move a row to a status outside
  ``assigned``/``done``.

Every invisibility assertion has a visibility control on the same footing.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.homework.service import HomeworkService
    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_TREATING = "3d8f2a61-7c4e-5b19-8a02-6f1e9c3b7d54"
_STRANGER = "9a1c7e30-4f2b-5d68-b7e1-2c5a8d0f6e93"

_SOAP_PLAN = {
    "plan": {
        "interventions_used": [{"text": "Cognitive restructuring."}],
        "homework_assignments": [
            {"text": "Client will complete two thought records."},
            {"text": "None assigned."},
            {"text": "Practice paced breathing before bed."},
        ],
        "next_steps": [{"text": "Review thought records next session."}],
    }
}


@pytest.fixture(scope="module", autouse=True)
def _builtin_note_types() -> None:
    """The built-in types, registered as the app does at startup."""
    from app.notes import get_default_registry, register_builtin_note_types  # noqa: PLC0415

    registry = get_default_registry()
    if not registry.has("soap"):
        register_builtin_note_types(registry)


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_homework_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as_clinician(engine: Engine, schema: str, user_id: str = _TREATING) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient_id})
    return conn


def _new_patient(engine: Engine, schema: str) -> str:
    """A patient the treating clinician holds a grant on. One per test, so tests stay apart."""
    patient_id = str(uuid.uuid4())
    conn = _as_clinician(engine, schema)
    try:
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Bayer', 'Mountain', 'bayer', 'mountain', "
                "'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _TREATING},
        )
        conn.commit()
    finally:
        conn.close()
    return patient_id


def _note(  # noqa: PLR0913 — one keyword per column the proof depends on
    engine: Engine,
    schema: str,
    patient_id: str,
    note_type: str,
    content: dict[str, Any],
    *,
    days_ago: int,
    signed: bool = True,
    restricted: bool = False,
) -> str:
    note_id = str(uuid.uuid4())
    at = datetime.now(UTC) - timedelta(days=days_ago)
    conn = _as_clinician(engine, schema)
    try:
        conn.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, content, finalized_at, "
                "restricted, author_user_id, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :type, "
                "CAST(:content AS jsonb), :signed, :restricted, CAST(:u AS uuid), :at, :at)"
            ),
            {
                "id": note_id,
                "pid": patient_id,
                "type": note_type,
                "content": json.dumps(content),
                "signed": at if signed else None,
                "restricted": restricted,
                "u": _TREATING,
                "at": at,
            },
        )
        conn.commit()
    finally:
        conn.close()
    return note_id


@contextmanager
def _clinician_service(schema: str, user_id: str = _TREATING) -> Iterator[HomeworkService]:
    from app.db.tenant_session import tenant_db_session  # noqa: PLC0415
    from app.homework.repository import HomeworkRepository  # noqa: PLC0415
    from app.homework.service import HomeworkService  # noqa: PLC0415

    with tenant_db_session(schema, user_id) as session:
        yield HomeworkService(HomeworkRepository(session))


def _suggest(
    schema: str, note_id: str, patient_id: str, note_type: str, content: dict[str, Any]
) -> None:
    """The note's homework brought up to date, as the chart-update step does after a save."""
    from app.db.tenant_session import tenant_db_session  # noqa: PLC0415
    from app.homework.proposals import refresh_homework  # noqa: PLC0415
    from app.notes import get_default_registry  # noqa: PLC0415
    from app.repositories.postgres.chart_proposals import (  # noqa: PLC0415
        PostgresChartProposalRepository,
    )

    with tenant_db_session(schema, _TREATING) as session:
        refresh_homework(
            PostgresChartProposalRepository(session),
            SimpleNamespace(id=note_id, patient_id=patient_id),  # type: ignore[arg-type]
            get_default_registry().get(note_type),
            content,
        )


def _suggestions(schema: str, note_id: str, user_id: str = _TREATING) -> list[Any]:
    """The note's homework chart proposals, as the chart-updates review lists them."""
    from app.db.tenant_session import tenant_db_session  # noqa: PLC0415
    from app.repositories.postgres.chart_proposals import (  # noqa: PLC0415
        PostgresChartProposalRepository,
    )

    with tenant_db_session(schema, user_id) as session:
        listed = PostgresChartProposalRepository(session).list_for_note(note_id)
        return sorted((p for p in listed if p.field_key == "homework"), key=lambda p: p.item_key)


def _decide(  # noqa: PLR0913 — the note, client, suggestion and decision a review sends
    schema: str,
    note_id: str,
    patient_id: str,
    proposal_id: str,
    decision: str,
    text: str | None = None,
) -> Any:
    """Decide one suggestion through the chart-proposal service, as the sign-time review does."""
    from app.chart_proposals.families import ChartWriters  # noqa: PLC0415
    from app.chart_proposals.service import ChartProposalService, Choice  # noqa: PLC0415
    from app.db.tenant_session import tenant_db_session  # noqa: PLC0415
    from app.homework.repository import HomeworkRepository  # noqa: PLC0415
    from app.repositories.postgres.chart_proposals import (  # noqa: PLC0415
        PostgresChartProposalRepository,
    )

    with tenant_db_session(schema, _TREATING) as session:
        # The homework family writes through the homework writer alone.
        writers = ChartWriters(
            history=None,  # type: ignore[arg-type]
            patients=None,  # type: ignore[arg-type]
            homework=HomeworkRepository(session),
        )
        service = ChartProposalService(PostgresChartProposalRepository(session), writers)
        return service.decide(
            SimpleNamespace(id=note_id),  # type: ignore[arg-type]
            SimpleNamespace(id=patient_id),  # type: ignore[arg-type]
            proposal_id,
            Choice(decision, text),  # type: ignore[arg-type]
            _TREATING,
        )


@contextmanager
def _patient_service(engine: Engine, schema: str, patient_id: str) -> Iterator[HomeworkService]:
    """The service on an ORM session armed for a patient principal (as the refill suite does)."""
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
        arm_current_patient_id,
    )
    from app.homework.repository import HomeworkRepository  # noqa: PLC0415
    from app.homework.service import HomeworkService  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(schema)
    user_token = _current_user_id.set(None)
    patient_token = _current_patient_id.set(patient_id)
    session = Session(bind=engine)
    try:
        session.execute(text(f"SET search_path = {schema}, platform, public"))
        arm_current_patient_id(session, patient_id)
        yield HomeworkService(HomeworkRepository(session))
        session.commit()
    finally:
        session.close()
        _current_patient_id.reset(patient_token)
        _current_user_id.reset(user_token)
        _current_tenant_schema.reset(schema_token)


def _statuses(engine: Engine, schema: str, patient_id: str) -> dict[str, str]:
    conn = _as_clinician(engine, schema)
    try:
        rows = conn.execute(
            text(
                "SELECT id::text, status FROM homework_assignments "
                "WHERE patient_id = CAST(:p AS uuid)"
            ),
            {"p": patient_id},
        ).all()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def _soap_note(engine: Engine, schema: str, patient: str, *, signed: bool = False) -> str:
    """A SOAP note with its homework suggested, as a saved draft has it."""
    note = _note(engine, schema, patient, "soap", _SOAP_PLAN, days_ago=1, signed=signed)
    _suggest(schema, note, patient, "soap", _SOAP_PLAN)
    return note


class TestTableShipped:
    def test_present_with_rls_forced_and_a_policy(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = 'homework_assignments'"
                    ),
                    {"s": tenant_schema},
                )
                .mappings()
                .first()
            )
        assert row is not None, "homework_assignments missing from a freshly provisioned tenant"
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policies"] >= 1

    def test_template_columns_match_the_model(self, engine: Engine, tenant_schema: str) -> None:
        from app.db.models import HomeworkAssignmentRow  # noqa: PLC0415

        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'homework_assignments'"
                    ),
                    {"s": tenant_schema},
                )
                .scalars()
                .all()
            )
        assert columns == {c.name for c in HomeworkAssignmentRow.__table__.columns}


class TestSuggestionsBesideTheNote:
    def test_stored_as_the_notes_chart_proposals_and_kept_current_on_save(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient = _new_patient(engine, tenant_schema)
        note = _soap_note(engine, tenant_schema, patient)
        suggested = _suggestions(tenant_schema, note)
        assert [(p.item_key, p.proposed_text, p.origin) for p in suggested] == [
            ("plan.homework_assignments#0", "Complete two thought records.", "homework"),
            ("plan.homework_assignments#2", "Practice paced breathing before bed.", "homework"),
        ]
        assert _statuses(engine, tenant_schema, patient) == {}, "nothing is assigned yet"

        edited = {"plan": {"homework_assignments": [{"text": "Complete three thought records."}]}}
        _suggest(tenant_schema, note, patient, "soap", edited)
        assert [p.proposed_text for p in _suggestions(tenant_schema, note)] == [
            "Complete three thought records."
        ]

    def test_a_clinician_without_a_grant_sees_none_of_them(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient = _new_patient(engine, tenant_schema)
        note = _soap_note(engine, tenant_schema, patient)
        assert len(_suggestions(tenant_schema, note)) == 2  # control
        assert _suggestions(tenant_schema, note, _STRANGER) == []


class TestAcceptingASuggestion:
    def test_accept_edit_and_dismiss_go_through_the_chart_update_review(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.chart_proposals.service import ProposalDecidedError  # noqa: PLC0415

        patient = _new_patient(engine, tenant_schema)
        note = _soap_note(engine, tenant_schema, patient)
        first, second = _suggestions(tenant_schema, note)

        _decide(tenant_schema, note, patient, first.id, "edit", "Complete two thought records")
        _decide(tenant_schema, note, patient, second.id, "discard")
        with pytest.raises(ProposalDecidedError):
            _decide(tenant_schema, note, patient, second.id, "accept")

        with _clinician_service(tenant_schema) as service:
            (assigned,) = service.overview(patient).assignments
        assert assigned.status == "assigned"
        assert assigned.text == "Complete two thought records"
        assert assigned.proposed_text == "Complete two thought records."
        assert assigned.proposal_id == first.id
        assert assigned.note_id == note
        assert assigned.source_field == "plan.homework_assignments"
        assert assigned.origin == "note"

        # A save after deciding offers neither again.
        _suggest(tenant_schema, note, patient, "soap", _SOAP_PLAN)
        assert [p.decision for p in _suggestions(tenant_schema, note)] == ["edited", "discarded"]

    def test_a_stranger_reaches_no_assignment(self, engine: Engine, tenant_schema: str) -> None:
        patient = _new_patient(engine, tenant_schema)
        with _clinician_service(tenant_schema) as service:
            service.add(patient, "Walk after dinner", _TREATING)  # control
        with _clinician_service(tenant_schema, _STRANGER) as service:
            assert service.overview(patient).assignments == []


class TestClient:
    def _assigned_and_withdrawn(self, engine: Engine, schema: str) -> tuple[str, str, str]:
        patient = _new_patient(engine, schema)
        note = _soap_note(engine, schema, patient)
        first, second = _suggestions(schema, note)
        _decide(schema, note, patient, first.id, "accept")
        _decide(schema, note, patient, second.id, "accept")
        with _clinician_service(schema) as service:
            kept, gone = sorted(service.overview(patient).assignments, key=lambda a: a.text)
            service.withdraw(patient, gone.id, _TREATING)
        return patient, kept.id, gone.id

    def test_sees_only_open_and_done_homework_through_the_row_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient, kept, withdrawn = self._assigned_and_withdrawn(engine, tenant_schema)
        other, other_kept, _ = self._assigned_and_withdrawn(engine, tenant_schema)
        conn = _as_patient(engine, tenant_schema, patient)
        try:
            visible = set(conn.execute(text("SELECT id::text FROM homework_assignments")).scalars())
            suggestions = conn.execute(
                text("SELECT count(*) FROM note_chart_proposals WHERE field_key = 'homework'")
            ).scalar()
        finally:
            conn.close()
        assert kept in visible  # control
        assert withdrawn not in visible
        assert other_kept not in visible
        assert other != patient
        assert suggestions == 0, "a suggestion is never the client's to read"

    def test_cannot_move_a_row_outside_assigned_or_done(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        patient, kept, _ = self._assigned_and_withdrawn(engine, tenant_schema)
        conn = _as_patient(engine, tenant_schema, patient)
        try:
            conn.execute(
                text("UPDATE homework_assignments SET status = 'done' WHERE id = CAST(:i AS uuid)"),
                {"i": kept},
            )  # control
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "UPDATE homework_assignments SET status = 'withdrawn' "
                        "WHERE id = CAST(:i AS uuid)"
                    ),
                    {"i": kept},
                )
        finally:
            conn.rollback()
            conn.close()

    def test_marks_done_with_a_note_and_back_and_never_reaches_a_withdrawn_one(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.homework.service import (  # noqa: PLC0415
            HomeworkDecisionError,
            HomeworkNotFoundError,
        )

        patient, kept, withdrawn = self._assigned_and_withdrawn(engine, tenant_schema)
        with _patient_service(engine, tenant_schema, patient) as service:
            assert [a.id for a in service.list_for_client(patient)] == [kept]
            done = service.mark(patient, kept, done=True, note="Did one on Tuesday")
        assert done.status == "done"
        assert done.client_note == "Did one on Tuesday"
        assert done.completed_at is not None

        with _patient_service(engine, tenant_schema, patient) as service:
            with pytest.raises(HomeworkDecisionError):
                service.mark(patient, kept, done=True)
            with pytest.raises(HomeworkNotFoundError):
                service.mark(patient, withdrawn, done=True)
            undone = service.mark(patient, kept, done=False)
        assert undone.status == "assigned"
        assert undone.client_note is None
