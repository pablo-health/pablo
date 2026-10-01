# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for Google: where the connection goes, and when it is allowed to.

``google_calendar_base_url`` points the OAuth flow and every Calendar API
call at one origin instead of Google's three hosts. It is only ever honoured
in a development environment — the end-to-end stack — so a deployment that
carries it by mistake keeps talking to Google.

Also here: the route that runs the scheduled calendar pass for one account
on request, which is how that stack reads a calendar it has just changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.models.audit import AuditAction
from app.rate_limit import get_calendar_sync_limiter
from app.repositories.audit import InMemoryAuditRepository
from app.routes.outside_sessions import get_sync_scheduler
from app.services import get_audit_service
from app.services.audit_service import AuditService
from app.services.google_calendar_service import (
    _build_calendar_service,
    _build_flow,
    google_consent_surface,
)
from app.services.sync_scheduler_service import ExecuteSummary
from app.settings import Settings, get_settings
from google.oauth2.credentials import Credentials
from pydantic import SecretStr

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

STAND_IN = "http://localhost:8090"


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql://x:x@localhost:5432/x",
        "google_calendar_client_id": "client",
        "google_calendar_base_url": STAND_IN,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]


class TestWhereTheSurfacePoints:
    def test_development_honours_the_stand_in(self) -> None:
        surface = google_consent_surface(_settings(environment="development"))
        assert surface.base_url == STAND_IN

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_anywhere_else_refuses_to_boot_with_it(self, environment: str) -> None:
        with pytest.raises(ValueError, match="GOOGLE_CALENDAR_BASE_URL must not be set"):
            _settings(environment=environment)

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_the_surface_ignores_it_anyway(self, environment: str) -> None:
        # Belt and braces: settings that somehow carry the value outside
        # development (the validator is bypassed here) still build a surface
        # aimed at Google.
        carried = SimpleNamespace(
            google_calendar_client_id="client",
            google_calendar_client_secret=SecretStr("secret"),
            google_calendar_base_url=STAND_IN,
            is_development=environment == "development",
        )
        surface = google_consent_surface(carried)  # type: ignore[arg-type]
        assert surface.base_url is None

    def test_unset_means_google(self) -> None:
        surface = google_consent_surface(
            _settings(environment="development", google_calendar_base_url=None)
        )
        assert surface.base_url is None


class TestWhereTheCallsGo:
    def test_every_calendar_client_is_built_where_the_surface_points(self) -> None:
        """Only ``_calendar`` builds a client, so none can skip the stand-in.

        A call built straight from ``_build_calendar_service`` goes to Google
        whatever the surface says: on the end-to-end stack that is a 401 from
        the real API in the middle of a read of the stand-in.
        """
        import ast  # noqa: PLC0415
        import inspect  # noqa: PLC0415

        from app.services import google_calendar_service  # noqa: PLC0415

        tree = ast.parse(inspect.getsource(google_calendar_service))
        callers = {
            function.name
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_build_calendar_service"
        }
        assert callers == {"_calendar"}

    def test_flow_uses_googles_hosts_by_default(self) -> None:
        flow = _build_flow("id", "secret", "http://localhost:3000/x", ["scope"])
        assert flow.client_config["auth_uri"] == "https://accounts.google.com/o/oauth2/auth"
        assert flow.client_config["token_uri"] == "https://oauth2.googleapis.com/token"

    def test_flow_uses_the_stand_ins_paths(self) -> None:
        flow = _build_flow(
            "id", "secret", "http://localhost:3000/x", ["scope"], base_url=STAND_IN + "/"
        )
        assert flow.client_config["auth_uri"] == f"{STAND_IN}/o/oauth2/auth"
        assert flow.client_config["token_uri"] == f"{STAND_IN}/token"

    def test_calendar_client_is_built_offline_and_aimed_at_the_stand_in(self) -> None:
        service = _build_calendar_service(_unused_credentials(), base_url=STAND_IN)
        request = service.events().list(calendarId="primary", singleEvents=True)
        assert request.uri.startswith(f"{STAND_IN}/calendar/v3/calendars/primary/events")

    def test_calendar_client_reaches_google_by_default(self) -> None:
        service = _build_calendar_service(_unused_credentials())
        request = service.calendarList().get(calendarId="primary")
        assert request.uri.startswith("https://www.googleapis.com/calendar/v3/users/me/")


def _unused_credentials() -> Credentials:
    """Enough to build a client; nothing here is ever sent."""
    return Credentials(None)  # type: ignore[no-untyped-call]


@dataclass
class _Scheduler:
    ran_for: list[str] = field(default_factory=list)

    def execute(self, user_id: str) -> ExecuteSummary:
        self.ran_for.append(user_id)
        return ExecuteSummary(google_synced=True, outside_sessions_followed=2, reminders_sent=1)


class TestReadingTheCalendarsNow:
    def test_runs_the_scheduled_pass_for_the_caller(
        self, client: TestClient, mock_user_id: str
    ) -> None:
        scheduler = _Scheduler()
        audit_repo = InMemoryAuditRepository()
        app.dependency_overrides[get_sync_scheduler] = lambda: scheduler
        app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)

        response = client.post("/api/calendar/sync")

        assert response.status_code == 200, response.text
        assert scheduler.ran_for == [mock_user_id]
        body = response.json()
        assert body["google_synced"] is True
        assert body["outside_sessions_followed"] == 2
        assert body["reminders_sent"] == 1
        # Audited in counts, never in content, under its own action.
        [entry] = audit_repo.list_for_user(mock_user_id)
        assert entry.action == AuditAction.CALENDAR_SYNCED
        assert entry.resource_id == "calendar-sync"
        assert entry.changes is not None
        assert entry.changes["outside_sessions_followed"] == 2

    def test_is_limited_per_user(self, client: TestClient) -> None:
        scheduler = _Scheduler()
        app.dependency_overrides[get_sync_scheduler] = lambda: scheduler
        app.dependency_overrides[get_audit_service] = lambda: AuditService(
            InMemoryAuditRepository()
        )
        limiter = get_calendar_sync_limiter()
        limiter.reset()
        try:
            allowed = get_settings().calendar_sync_rate_per_min
            for _ in range(allowed):
                assert client.post("/api/calendar/sync").status_code == 200
            assert client.post("/api/calendar/sync").status_code == 429
            # The refused call never reached the pass.
            assert len(scheduler.ran_for) == allowed
        finally:
            limiter.reset()
