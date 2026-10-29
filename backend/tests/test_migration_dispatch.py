# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An import's preview and apply run off the request: queued on Cloud Tasks
where a queue is configured, as request background tasks in development.

The worker route takes only ids, resolves the tenant from the user, and is
reachable by the Cloud Tasks invoker alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest
from app.routes import migration as migration_routes
from app.routes.migration import ImportStepJob, _dispatch
from app.settings import Settings
from fastapi import BackgroundTasks

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


class _Ctx:
    practice_id = "practice-1"
    schema_name = "practice_abc"


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch) -> _Ctx:
    monkeypatch.setattr(migration_routes, "_schema", lambda _ctx: "practice_abc")
    return _Ctx()


def _settings(environment: Literal["development", "staging", "production"]) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment=environment,
        import_task_queue="pablo-imports",
    )


def test_in_development_the_steps_run_as_request_background_tasks(
    monkeypatch: pytest.MonkeyPatch, ctx: _Ctx
) -> None:
    monkeypatch.setattr(migration_routes, "get_settings", lambda: _settings("development"))
    queued: list[Any] = []
    monkeypatch.setattr(migration_routes, "enqueue", lambda *a, **k: queued.append((a, k)))
    background = BackgroundTasks()

    _dispatch(background, "preview", ctx, "user-1", "run-1")  # type: ignore[arg-type]
    _dispatch(background, "apply", ctx, "user-1", "run-1")  # type: ignore[arg-type]

    assert queued == []
    assert [t.func for t in background.tasks] == [
        migration_routes.run_preview,
        migration_routes.run_apply,
    ]
    assert background.tasks[0].args == ("practice_abc", "user-1", "run-1")
    assert background.tasks[1].args == ("practice_abc", "user-1", "run-1", "practice-1")


def test_elsewhere_the_steps_are_queued_with_ids_only(
    monkeypatch: pytest.MonkeyPatch, ctx: _Ctx
) -> None:
    monkeypatch.setattr(migration_routes, "get_settings", lambda: _settings("production"))
    queued: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        migration_routes, "enqueue", lambda *a, **k: queued.append((a, k.get("dedup_key")))
    )
    background = BackgroundTasks()

    _dispatch(background, "apply", ctx, "user-1", "run-1")  # type: ignore[arg-type]

    assert background.tasks == []
    ((queue, path, payload), dedup) = queued[0]
    assert queue == "pablo-imports"
    assert path == "/api/internal/jobs/import-apply"
    # Ids only: the worker resolves the tenant from the user, never from the payload.
    assert payload == {"user_id": "user-1", "run_id": "run-1"}
    assert dedup == "import-apply-run-1"


def test_the_worker_route_is_closed_to_anyone_but_the_invoker(client: TestClient) -> None:
    body = ImportStepJob(user_id="user-1", run_id="run-1").model_dump()
    for step in ("preview", "apply"):
        response = client.post(f"/api/internal/jobs/import-{step}", json=body)
        assert response.status_code in (401, 403, 503), response.text
