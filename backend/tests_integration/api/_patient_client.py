# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A test client for the patient portal routes, and the principal it carries."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING, Any

from app.auth.patient_context import AuthStrength, PatientContext
from app.db import arm_current_patient_id, get_db_session, set_tenant_schema
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI


def armed_patient_context(
    schema: str, patient_id: str, strength: AuthStrength = AuthStrength.STEPPED_UP
) -> PatientContext:
    """Stand in for ``get_patient_context`` and for what it arms on the request session.

    A bare context would leave the routes on the template schema with no principal.
    """
    context = PatientContext(
        patient_id=patient_id,
        practice_schema=schema,
        credential_kind="bearer",
        auth_strength=strength,
    )
    session = get_db_session()
    set_tenant_schema(session, schema)
    arm_current_patient_id(session, patient_id)
    return context


class _FreshContextClient(TestClient):
    def request(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return contextvars.Context().run(super().request, *args, **kwargs)


def fresh_context_client(app: FastAPI) -> TestClient:
    """A TestClient whose every request starts from an empty context, as under a server.

    Seeding arms a clinician in the pytest thread, and TestClient would hand that
    to the app, so a patient request would carry a clinician id. https, because
    SecurityHeadersMiddleware refuses plain HTTP on PHI routes.
    """
    return _FreshContextClient(app, base_url="https://testserver", raise_server_exceptions=False)
