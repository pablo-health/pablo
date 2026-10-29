# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``/api/tasks`` and the ``/api/compliance`` alias, over one real table.

Both routers run against a provisioned practice schema with the row policy
on, and every request re-arms ``app.current_user_id`` as the caller, the way
``get_tenant_context`` does in production. What this proves that the
in-memory route tests cannot:

* the two surfaces read and write the same rows — a compliance item made
  through the old API is a task through the new one, and the reverse;
* the alias's list holds compliance tasks only, so a client written before
  tasks never meets a row it has no template for;
* the filters, the self-referencing step link and its cascade, and the
  repeat rule work against Postgres and its constraints, not a stand-in;
* one clinician cannot reach another's task through either surface;
* a task tied to a client goes through that client's own access check, through
  the real grant table: linking or listing a client the caller holds no grant
  on is "not found", and a task whose client the caller can no longer open
  keeps the link without naming them.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from app.api_errors import register_exception_handlers
from app.auth.service import TenantContext, get_current_user, get_tenant_context
from app.db import arm_current_user_id, set_tenant_schema
from app.db.provisioning import create_practice_schema
from app.models import AuditAction, User
from app.repositories import get_compliance_item_repository, get_task_repository
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.task import PostgresComplianceItemRepository, PostgresTaskRepository
from app.routes import compliance as compliance_routes
from app.routes import tasks as task_routes
from app.routes.patients import get_patient_repository
from app.services import AuditService, get_audit_service
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SCHEMA = f"practice_test_tasks_{uuid.uuid4().hex[:8]}"
_ME = str(uuid.uuid4())
_SOMEONE_ELSE = str(uuid.uuid4())


def _user(user_id: str) -> User:
    return User(
        id=user_id,
        email=f"{user_id[:8]}@example.com",
        name="Test Therapist",
        created_at=datetime.now(UTC),
        baa_accepted_at=datetime.now(UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    create_practice_schema(eng, _SCHEMA)
    yield eng
    with eng.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.commit()
    eng.dispose()


class _Caller:
    """Who the next request is from. Switching it re-arms the row policy too."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.user_id = _ME

    def armed(self) -> Session:
        arm_current_user_id(self.session, self.user_id)
        return self.session


@pytest.fixture
def harness(engine: Engine) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    set_tenant_schema(session, _SCHEMA)
    caller = _Caller(session)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(compliance_routes.router)
    app.include_router(task_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _user(caller.user_id)
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=caller.user_id, practice_id="practice-1", practice_schema=_SCHEMA
    )
    app.dependency_overrides[get_task_repository] = lambda: PostgresTaskRepository(caller.armed())
    app.dependency_overrides[get_compliance_item_repository] = lambda: (
        PostgresComplianceItemRepository(caller.armed())
    )
    app.dependency_overrides[get_patient_repository] = lambda: PostgresPatientRepository(
        caller.armed()
    )
    audit_log = InMemoryAuditRepository()
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_log)
    try:
        yield {
            "client": TestClient(app),
            "caller": caller,
            "session": session,
            "audit": audit_log,
        }
    finally:
        session.rollback()
        for user_id in (_ME, _SOMEONE_ELSE):
            arm_current_user_id(session, user_id)
            session.execute(text("DELETE FROM tasks"))
            # A lapsed grant hides its client from the delete; renew it first.
            session.execute(text("UPDATE patient_clinicians SET expires_at = NULL"))
            session.execute(text("DELETE FROM patients"))
        session.commit()
        session.close()


# The helpers below write through the harness's own session, armed as whoever
# they act for: the requests run in that session's open transaction, so a
# second connection would neither see their rows nor get past their locks.


def _client_of(session: Session, clinician: str, first_name: str = "Omar") -> str:
    """A client in the practice, with ``clinician`` holding the grant to open them."""
    client_id = str(uuid.uuid4())
    arm_current_user_id(session, clinician)
    session.execute(
        text(
            "INSERT INTO patients (id, first_name, last_name, first_name_lower,"
            " last_name_lower, status, session_count, created_at, updated_at)"
            " VALUES (CAST(:pid AS uuid), :first, 'Haddad', lower(:first), 'haddad',"
            " 'active', 0, now(), now())"
        ),
        {"pid": client_id, "first": first_name},
    )
    _grant(session, clinician, client_id)
    return client_id


def _grant(session: Session, clinician: str, client_id: str) -> None:
    arm_current_user_id(session, clinician)
    session.execute(
        text(
            "INSERT INTO patient_clinicians (patient_id, user_id, granted_by)"
            " VALUES (CAST(:pid AS uuid), :u, :u)"
        ),
        {"pid": client_id, "u": clinician},
    )


def _set_grant(session: Session, clinician: str, client_id: str, *, live: bool) -> None:
    """Let ``clinician``'s grant on the client lapse, or renew it."""
    arm_current_user_id(session, clinician)
    session.execute(
        text(
            "UPDATE patient_clinicians SET expires_at = "
            "CASE WHEN :live THEN NULL ELSE now() - interval '1 day' END "
            "WHERE patient_id = CAST(:pid AS uuid)"
        ),
        {"pid": client_id, "live": live},
    )


def _stored_link(session: Session, task_id: str) -> str | None:
    arm_current_user_id(session, _ME)
    value: str | None = session.execute(
        text("SELECT patient_id::text FROM tasks WHERE id = CAST(:tid AS uuid)"),
        {"tid": task_id},
    ).scalar_one()
    return value


def _task(client: TestClient, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/tasks", json={"category": "practice", **fields})
    assert response.status_code == 201, response.text
    return response.json()


class TestOneTableTwoSurfaces:
    def test_a_compliance_item_is_a_compliance_task(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        made = client.post(
            "/api/compliance",
            json={"item_type": "license", "label": "NY LMHC", "due_date": "2027-06-30"},
        )
        assert made.status_code == 201, made.text
        item = made.json()
        # The old shape, field for field: a client pinned to it parses this.
        assert set(item) == {
            "id",
            "item_type",
            "label",
            "due_date",
            "notes",
            "completed_at",
            "created_at",
            "updated_at",
        }

        task = client.get(f"/api/tasks/{item['id']}").json()
        assert task["category"] == "compliance"
        assert task["source"] == "manual"
        assert task["item_type"] == "license"
        assert task["label"] == "NY LMHC"

    def test_the_alias_lists_compliance_tasks_only(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        _task(client, label="Renew the office lease", due_date="2027-01-15")
        licence = _task(client, category="compliance", item_type="license", label="LMHC")

        listed = client.get("/api/compliance").json()
        assert [i["id"] for i in listed] == [licence["id"]]
        assert {t["category"] for t in client.get("/api/tasks").json()} == {
            "practice",
            "compliance",
        }

    def test_the_alias_reaches_any_task_by_id(self, harness: dict[str, Any]) -> None:
        """A link handed out by either surface works on the other."""
        client = harness["client"]
        lease = _task(client, label="Renew the office lease", repeat_rule="yearly")

        done = client.post(f"/api/compliance/{lease['id']}/complete")
        assert done.status_code == 200, done.text
        assert done.json()["completed_at"] is not None

        # Updating through the old shape keeps what it does not carry.
        response = client.put(
            f"/api/compliance/{lease['id']}",
            json={"item_type": "custom", "label": "Lease, signed", "due_date": None},
        )
        assert response.status_code == 200, response.text
        after = client.get(f"/api/tasks/{lease['id']}").json()
        assert after["label"] == "Lease, signed"
        assert after["category"] == "practice"
        assert after["repeat_rule"] == "yearly"

        assert client.delete(f"/api/compliance/{lease['id']}").status_code == 204
        assert client.get(f"/api/tasks/{lease['id']}").status_code == 404


class TestTasks:
    def test_filters(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        plan = _task(client, label="Move my own billing in house", goal_key="goal-1")
        step = _task(
            client, label="Ask for the fee schedule", parent_task_id=plan["id"], source="plan"
        )
        _task(client, category="other", label="Order business cards")

        def ids(query: str) -> set[str]:
            response = client.get(f"/api/tasks?{query}")
            assert response.status_code == 200, response.text
            return {t["id"] for t in response.json()}

        assert ids(f"parent_task_id={plan['id']}") == {step["id"]}
        assert ids("goal_key=goal-1") == {plan["id"]}
        assert ids("source=plan") == {step["id"]}
        assert len(ids("category=practice")) == 2
        assert client.get("/api/tasks?category=chores").status_code == 422

    def test_deleting_a_plan_deletes_its_steps(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        plan = _task(client, label="Open a second office")
        step = _task(client, label="Tour two spaces", parent_task_id=plan["id"])

        assert client.delete(f"/api/tasks/{plan['id']}").status_code == 204
        assert client.get(f"/api/tasks/{step['id']}").status_code == 404

    def test_a_task_cannot_be_its_own_ancestor(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        plan = _task(client, label="Plan")
        step = _task(client, label="Step", parent_task_id=plan["id"])

        response = client.put(
            f"/api/tasks/{plan['id']}",
            json={"category": "practice", "label": "Plan", "parent_task_id": step["id"]},
        )
        assert response.status_code == 400, response.text

    def test_completing_a_repeating_task_files_the_next_one(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        rent = _task(client, label="Pay office rent", due_date="2027-01-31", repeat_rule="monthly")

        assert client.post(f"/api/tasks/{rent['id']}/complete").status_code == 200

        tasks = client.get("/api/tasks").json()
        [done] = [t for t in tasks if t["id"] == rent["id"]]
        [next_one] = [t for t in tasks if t["id"] != rent["id"]]
        assert done["completed_at"] is not None
        assert next_one["completed_at"] is None
        # Kept inside February rather than spilling into March.
        assert next_one["due_date"] == "2027-02-28"
        assert next_one["repeat_rule"] == "monthly"
        assert next_one["label"] == "Pay office rent"

    def test_a_compliance_task_follows_a_template(self, harness: dict[str, Any]) -> None:
        client = harness["client"]
        unknown = client.post(
            "/api/tasks",
            json={"category": "compliance", "item_type": "nope", "label": "x"},
        )
        assert unknown.status_code == 400
        assert unknown.json()["error"]["code"] == "UNKNOWN_ITEM_TYPE"

        # No template named: a compliance task with a title of its own.
        custom = _task(client, category="compliance", label="State board audit")
        assert custom["item_type"] == "custom"

        # Any other category follows none, whatever the client sends.
        plain = _task(client, item_type="license", label="Water the plants")
        assert plain["item_type"] == "task"


class TestIsolation:
    def test_another_clinicians_task_is_not_there(self, harness: dict[str, Any]) -> None:
        client, caller = harness["client"], harness["caller"]
        mine = _task(client, label="Mine")

        caller.user_id = _SOMEONE_ELSE
        assert client.get("/api/tasks").json() == []
        assert client.get(f"/api/tasks/{mine['id']}").status_code == 404
        assert client.post(f"/api/compliance/{mine['id']}/complete").status_code == 404
        assert client.delete(f"/api/tasks/{mine['id']}").status_code == 404
        # Nor can it be made a step of someone else's task.
        stolen = client.post(
            "/api/tasks",
            json={"category": "practice", "label": "x", "parent_task_id": mine["id"]},
        )
        assert stolen.status_code == 404

        caller.user_id = _ME
        assert client.get(f"/api/tasks/{mine['id']}").status_code == 200


class TestTasksAboutAClient:
    def test_a_client_the_caller_cannot_open_cannot_be_linked(
        self, harness: dict[str, Any]
    ) -> None:
        """The chart's own check: no grant, no link, and no hint the client exists."""
        client, session = harness["client"], harness["session"]
        theirs = _client_of(session, _SOMEONE_ELSE)

        response = client.post(
            "/api/tasks",
            json={"category": "practice", "label": "Send the release form", "patient_id": theirs},
        )
        assert response.status_code == 404, response.text
        assert client.get("/api/tasks").json() == []
        # Nor can an existing task be pointed at them.
        mine = _task(client, label="Send the release form")
        moved = client.put(
            f"/api/tasks/{mine['id']}",
            json={"category": "practice", "label": "Send the release form", "patient_id": theirs},
        )
        assert moved.status_code == 404
        assert _stored_link(session, mine["id"]) is None

    def test_a_linked_task_lists_on_its_clients_chart_by_name(
        self, harness: dict[str, Any]
    ) -> None:
        client, session = harness["client"], harness["session"]
        omar = _client_of(session, _ME, "Omar")
        linked = _task(client, label="Send the release form", patient_id=omar)
        done = _task(client, label="Call the school counselor", patient_id=omar)
        client.post(f"/api/tasks/{done['id']}/complete")
        _task(client, label="Renew the office lease")

        assert linked["patient_id"] == omar
        assert linked["patient_name"] == "Omar Haddad"
        chart = client.get(f"/api/tasks?patient_id={omar}&open_only=true")
        assert chart.status_code == 200, chart.text
        assert [t["id"] for t in chart.json()] == [linked["id"]]
        # The Tasks list names the client on the linked one and nobody elsewhere.
        names = {t["label"]: t["patient_name"] for t in client.get("/api/tasks").json()}
        assert names["Send the release form"] == "Omar Haddad"
        assert names["Renew the office lease"] is None

    def test_listing_another_clinicians_clients_tasks_is_not_found(
        self, harness: dict[str, Any]
    ) -> None:
        client, session = harness["client"], harness["session"]
        theirs = _client_of(session, _SOMEONE_ELSE)
        assert client.get(f"/api/tasks?patient_id={theirs}").status_code == 404

    def test_a_co_treating_clinician_does_not_see_anothers_task_about_the_client(
        self, harness: dict[str, Any]
    ) -> None:
        """Both hold a grant on the client; the task is still its owner's alone."""
        client, caller, session = harness["client"], harness["caller"], harness["session"]
        shared = _client_of(session, _ME)
        _grant(session, _SOMEONE_ELSE, shared)
        _task(client, label="Send the release form", patient_id=shared)

        caller.user_id = _SOMEONE_ELSE
        listed = client.get(f"/api/tasks?patient_id={shared}")
        assert listed.status_code == 200, listed.text
        assert listed.json() == []

    def test_losing_access_hides_the_client_and_keeps_the_link(
        self, harness: dict[str, Any]
    ) -> None:
        client, session = harness["client"], harness["session"]
        omar = _client_of(session, _ME)
        task = _task(client, label="Send the release form", patient_id=omar)

        _set_grant(session, _ME, omar, live=False)
        [listed] = client.get("/api/tasks").json()
        assert listed["id"] == task["id"]
        assert listed["patient_id"] is None
        assert listed["patient_name"] is None
        # Saving the task as it was shown does not cut a link the caller never saw.
        saved = client.put(
            f"/api/tasks/{task['id']}",
            json={"category": "practice", "label": "Send the signed release form"},
        )
        assert saved.status_code == 200, saved.text
        assert _stored_link(session, task["id"]) == omar

        _set_grant(session, _ME, omar, live=True)
        [back] = client.get("/api/tasks").json()
        assert back["patient_name"] == "Omar Haddad"

    def test_unlinking_a_client_the_caller_can_open(self, harness: dict[str, Any]) -> None:
        client, session = harness["client"], harness["session"]
        omar = _client_of(session, _ME)
        task = _task(client, label="Send the release form", patient_id=omar)

        saved = client.put(
            f"/api/tasks/{task['id']}",
            json={"category": "practice", "label": "Send the release form", "patient_id": None},
        )
        assert saved.status_code == 200, saved.text
        assert _stored_link(session, task["id"]) is None

    def test_a_response_naming_a_client_records_a_view_of_them(
        self, harness: dict[str, Any]
    ) -> None:
        client, session, audit = harness["client"], harness["session"], harness["audit"]
        omar = _client_of(session, _ME)
        _task(client, label="Send the release form", patient_id=omar)
        _task(client, label="Renew the office lease")

        client.get("/api/tasks")
        viewed = [e for e in audit.list_for_user(_ME) if e.action == AuditAction.PATIENT_VIEWED]
        assert viewed, "a task naming a client was returned with no record of the view"
        assert {e.patient_id for e in viewed} == {omar}

    def test_deleting_the_client_leaves_the_task_unlinked(self, harness: dict[str, Any]) -> None:
        client, session = harness["client"], harness["session"]
        omar = _client_of(session, _ME)
        task = _task(client, label="Send the release form", patient_id=omar)

        arm_current_user_id(session, _ME)
        session.execute(text("DELETE FROM patients WHERE id = CAST(:pid AS uuid)"), {"pid": omar})
        session.expire_all()

        assert client.get(f"/api/tasks/{task['id']}").status_code == 200
        assert _stored_link(session, task["id"]) is None
