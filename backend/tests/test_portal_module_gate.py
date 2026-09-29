# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A module a practice has turned off answers 404 to its clients.

Two halves. The gate itself, on a small app: off is the same 404 an unmounted
route gives, on is untouched, and a lookup that fails refuses. Then the
wiring on the real application, by calling it: every patient route of a
module is refused once the practice turns that module off — booking
included, as part of appointments — and a clinician route never asks.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated

import pytest
from app.auth.patient_context import AuthStrength, PatientContext, get_patient_context
from app.portal import module_gate
from app.portal.module_gate import require_portal_module
from app.portal.portal_settings import NOT_OFFERED, PortalSettings
from app.route_introspection import iter_api_routes
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

TENANT = "practice_abc123"


def _patient() -> PatientContext:
    return PatientContext(
        patient_id="11111111-1111-4111-8111-111111111111",
        practice_schema=TENANT,
        credential_kind="portal_session",
        auth_strength=AuthStrength.STEPPED_UP,
    )


def _app(module: str) -> FastAPI:
    router = APIRouter()

    @router.get("/api/patient/thing")
    def thing(patient: Annotated[PatientContext, Depends(get_patient_context)]) -> dict[str, str]:
        return {"schema": patient.practice_schema}

    application = FastAPI()
    application.include_router(router, dependencies=[Depends(require_portal_module(module))])
    application.dependency_overrides[get_patient_context] = _patient
    return application


def _settings(*modules: str) -> PortalSettings:
    return PortalSettings(enabled=True, enabled_modules=modules, decided_at=None)


def _lookup(settings: PortalSettings) -> Callable[[str], PortalSettings]:
    def _for_schema(schema: str) -> PortalSettings:
        assert schema == TENANT, "the gate asks about the caller's own practice"
        return settings

    return _for_schema


def test_a_module_the_practice_has_on_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module_gate, "portal_settings_for_schema", _lookup(_settings("refills")))

    response = TestClient(_app("refills")).get("/api/patient/thing")

    assert response.status_code == 200


def test_a_module_the_practice_turned_off_is_the_unmounted_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module_gate, "portal_settings_for_schema", _lookup(_settings("messaging")))
    client = TestClient(_app("refills"))

    turned_off = client.get("/api/patient/thing")
    never_mounted = client.get("/api/patient/nothing-here")

    assert turned_off.status_code == never_mounted.status_code == 404
    assert turned_off.json() == never_mounted.json()


def test_a_practice_that_never_chose_gets_every_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module_gate, "portal_settings_for_schema", _lookup(NOT_OFFERED))

    assert TestClient(_app("appointments")).get("/api/patient/thing").status_code == 200


def test_a_failed_lookup_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_schema: str) -> PortalSettings:
        raise RuntimeError("platform unavailable")

    monkeypatch.setattr(module_gate, "portal_settings_for_schema", _boom)

    assert TestClient(_app("refills")).get("/api/patient/thing").status_code == 404


# ── the wiring on the real application ──────────────────────────────────


#: Every patient-facing route prefix that belongs to a module, and the module.
#: Booking is part of appointments.
MODULE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/patient/intake", "intake"),
    ("/api/patient/messages", "messaging"),
    ("/api/patient/appointments", "appointments"),
    ("/api/patient/booking", "appointments"),
    ("/api/patient/refills", "refills"),
)


@pytest.fixture
def real_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FastAPI, list[str]]]:
    """The assembled application, with a signed-in client whose practice has
    turned EVERY module off, and a record of each gate lookup."""
    from app.main import app  # noqa: PLC0415

    looked_up: list[str] = []

    def _nothing_on(schema: str) -> PortalSettings:
        looked_up.append(schema)
        return PortalSettings(enabled=True, enabled_modules=(), decided_at=None)

    monkeypatch.setattr(module_gate, "portal_settings_for_schema", _nothing_on)
    app.dependency_overrides[get_patient_context] = _patient
    try:
        yield app, looked_up
    finally:
        app.dependency_overrides.pop(get_patient_context, None)


def _concrete(path: str) -> str:
    """A requestable path from a template: every ``{param}`` becomes ``x``."""
    return re.sub(r"\{[^}]+\}", "x", path)


@pytest.mark.parametrize(("prefix", "module"), MODULE_PREFIXES)
def test_every_patient_route_of_a_module_is_refused_when_the_practice_turned_it_off(
    real_app: tuple[FastAPI, list[str]], prefix: str, module: str
) -> None:
    """Behaviour, not introspection: each route is actually called. (Since
    fastapi 0.137 an ``include_router`` dependency is not visible on the
    declared route, so reading it back would prove nothing.)"""
    app, looked_up = real_app
    routes = [
        (path, method)
        for path, route in iter_api_routes(app)
        if path.startswith(prefix)
        for method in route.methods
    ]
    if not routes:
        pytest.skip(f"{module} is not mounted in this configuration")

    client = TestClient(app, raise_server_exceptions=False)
    for path, method in routes:
        looked_up.clear()
        response = client.request(method, _concrete(path))
        assert response.status_code == 404, f"{method} {path} answered {response.status_code}"
        assert looked_up == [TENANT], f"{method} {path} did not ask the {module} gate"


def test_a_clinician_route_never_asks_the_gate(real_app: tuple[FastAPI, list[str]]) -> None:
    """A practice reading what its clients sent is not a portal module."""
    app, looked_up = real_app

    TestClient(app, raise_server_exceptions=False).get("/api/patients/x/message-threads")

    assert looked_up == []
