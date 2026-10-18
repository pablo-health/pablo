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
* one clinician cannot reach another's task through either surface.
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
from app.models import User
from app.repositories import get_compliance_item_repository, get_task_repository
from app.repositories.postgres.task import PostgresComplianceItemRepository, PostgresTaskRepository
from app.routes import compliance as compliance_routes
from app.routes import tasks as task_routes
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
    try:
        yield {"client": TestClient(app), "caller": caller, "session": session}
    finally:
        session.rollback()
        for user_id in (_ME, _SOMEONE_ELSE):
            arm_current_user_id(session, user_id)
            session.execute(text("DELETE FROM tasks"))
        session.commit()
        session.close()


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
