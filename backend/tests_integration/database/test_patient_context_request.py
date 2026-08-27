# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``get_patient_context`` through a real FastAPI request, against real Postgres.

Every other test of this dependency replaces the database-arming path with
mocks — which is exactly why the sharpest bug in the seam survived them.
``set_tenant_schema`` writes a ContextVar, and FastAPI runs a *sync*
dependency in a throwaway threadpool worker whose context copy is
discarded on return. The GUC survived that hop because it also rides
``Session.info``; the schema had no such carrier, so it was silently lost.
Nothing was visibly wrong until the request's first mid-request commit
released the connection — the next checkout then re-stamped ``search_path``
from the value the *middleware* left, which for a patient request is the
shared ``practice`` template, while the patient GUC stayed correctly armed.

The second half of such a request would read and write the template schema
under a live patient identity. In a single-practice deployment ``practice``
IS the clinical data and carries no row policies at all.

So these tests run the real stack: the real ``DatabaseSessionMiddleware``,
the real dependency, a real resolver, a real provisioned tenant schema, and
a real ``release_db_connection()``-shaped mid-request commit — the pattern
every LLM path in this codebase uses before calling a model, and the one a
companion chat will use.

If ``get_patient_context`` is ever changed back to ``def``, these fail.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

from app.auth.patient_context import (  # noqa: E402
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    get_patient_context,
    get_patient_resolver_registry,
)
from app.db import DEFAULT_PRACTICE_SCHEMA, get_db_session  # noqa: E402
from app.db.middleware import DatabaseSessionMiddleware  # noqa: E402

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_pcreq_{_SUFFIX}"
_PATIENT = "5d3a1f92-6c4b-4e18-9a70-2f8b5c1d3e46"
_TOKEN = "a-valid-patient-credential"  # noqa: S105 - a test credential, not a secret


class _SchemaResolver:
    """A front door that maps one credential to one patient in one schema."""

    credential_kind = "bearer"

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        if credential.value != _TOKEN:
            return None
        return PatientContext(
            patient_id=_PATIENT,
            practice_schema=_SCHEMA,
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )


@pytest.fixture(scope="module")
def schema() -> Iterator[str]:
    engine = create_engine(_DB_URL)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {_SCHEMA}.probe (id int PRIMARY KEY)"))
    yield _SCHEMA
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    engine.dispose()


def _read_state() -> dict[str, str]:
    """What the database actually thinks this request is, right now."""
    session = get_db_session()
    return {
        "search_path": (session.execute(text("SHOW search_path")).scalar() or ""),
        "patient_guc": (
            session.execute(text("SELECT current_setting('app.current_patient_id', true)")).scalar()
            or ""
        ),
        "user_guc": (
            session.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
            or ""
        ),
    }


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(DatabaseSessionMiddleware)

    registry = PatientResolverRegistry()
    registry.register(_SchemaResolver())
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry

    @app.get("/patient/state")
    async def _state_async(
        _ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, dict[str, str]]:
        before = _read_state()
        # The release-the-connection-before-a-slow-call pattern: this is
        # what every LLM path here does, and what a companion chat will do.
        get_db_session().commit()
        after = _read_state()
        return {"before": before, "after": after}

    @app.get("/patient/state-sync")
    def _state_sync(
        _ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, dict[str, str]]:
        before = _read_state()
        get_db_session().commit()
        after = _read_state()
        return {"before": before, "after": after}

    return app


@pytest.mark.usefixtures("schema")
class TestPatientRequestKeepsItsSchema:
    @pytest.mark.parametrize("route", ["/patient/state", "/patient/state-sync"])
    def test_schema_and_guc_survive_a_mid_request_commit(self, route: str) -> None:
        client = TestClient(_build_app())

        response = client.get(route, headers={"Authorization": f"Bearer {_TOKEN}"})

        assert response.status_code == 200
        body = response.json()

        # Control: the dependency armed the request correctly to begin with.
        assert _SCHEMA in body["before"]["search_path"], (
            f"the dependency never armed the patient's schema: {body['before']}"
        )
        assert body["before"]["patient_guc"] == _PATIENT

        # The actual assertion: still true after the connection was released
        # and reacquired. This is what a sync dependency silently lost.
        assert _SCHEMA in body["after"]["search_path"], (
            "search_path reverted after a mid-request commit — the rest of "
            "this request would run against "
            f"{body['after']['search_path']!r} with the patient GUC still "
            f"armed at {body['after']['patient_guc']!r}"
        )
        assert body["after"]["patient_guc"] == _PATIENT

    @pytest.mark.parametrize("route", ["/patient/state", "/patient/state-sync"])
    def test_the_request_never_lands_on_the_shared_template_schema(self, route: str) -> None:
        """Named separately because this is the specific harm.

        ``enable_rls_on_schema`` skips ``DEFAULT_PRACTICE_SCHEMA``, so that
        schema has no row policies at all. Landing there with a patient
        principal armed is unfiltered access in a single-practice
        deployment, and silent wrong-data in a multi-tenant one.
        """
        client = TestClient(_build_app())
        body = client.get(route, headers={"Authorization": f"Bearer {_TOKEN}"}).json()

        first_schema = body["after"]["search_path"].split(",")[0].strip().strip('"')
        assert first_schema != DEFAULT_PRACTICE_SCHEMA, (
            "the request fell back to the shared template schema after a commit"
        )

    def test_the_clinician_guc_is_never_armed_on_a_patient_request(self) -> None:
        """Both principals on one transaction would union their grants."""
        client = TestClient(_build_app())
        body = client.get("/patient/state", headers={"Authorization": f"Bearer {_TOKEN}"}).json()

        assert body["before"]["user_guc"] == ""
        assert body["after"]["user_guc"] == ""

    def test_an_unresolvable_credential_is_401_through_the_real_stack(self) -> None:
        client = TestClient(_build_app())
        response = client.get("/patient/state", headers={"Authorization": "Bearer not-the-token"})
        assert response.status_code == 401

    def test_a_lowercase_scheme_still_resolves_a_genuine_patient(self) -> None:
        """The case-insensitivity fix must not break the patient path itself."""
        client = TestClient(_build_app())
        response = client.get("/patient/state", headers={"Authorization": f"bearer {_TOKEN}"})
        assert response.status_code == 200
        assert response.json()["before"]["patient_guc"] == _PATIENT
