# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The client's half of homework is a portal module, and its answer is an allow-list.

What this catches: the patient routes being served by a deployment that did
not name the module (so "the portal doesn't show it" would be the only
thing in front of them); the capability marker drifting from the real path
(the module would report off forever); and a field that quotes the note, or
says who decided, leaking onto the client's response.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.homework.repository import Assignment
from app.homework.schemas import PatientHomeworkResponse
from app.main import portal_module_routers
from app.portal.modules import MODULE_MARKER_PATHS, PORTAL_MODULE_NAMES
from app.routes.homework import patient_homework_router


def test_mounted_only_when_the_deployment_names_it() -> None:
    assert patient_homework_router not in portal_module_routers(["intake", "messaging"])
    assert patient_homework_router in portal_module_routers(["homework"])


def test_the_capability_marker_is_a_real_patient_path() -> None:
    assert "homework" in PORTAL_MODULE_NAMES
    paths = {route.path for route in patient_homework_router.routes}  # type: ignore[attr-defined]
    assert MODULE_MARKER_PATHS["homework"] in paths


def test_the_client_never_sees_the_note_or_who_decided() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assignment = Assignment(
        id="a1",
        patient_id="p1",
        note_id="n1",
        proposal_id="cp1",
        source_field="plan.homework_assignments",
        proposed_text="Complete two thought records; trauma history discussed.",
        text="Complete two thought records",
        status="assigned",
        origin="note",
        assigned_at=now,
        completed_at=None,
        client_note=None,
        created_at=now,
        updated_at=now,
    )
    shown = PatientHomeworkResponse.from_assignment(assignment).model_dump()
    assert set(shown) == {"id", "text", "status", "assigned_at", "completed_at", "client_note"}
    assert "trauma" not in str(shown)
