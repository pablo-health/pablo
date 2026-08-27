# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""IDOR against the patient principal, through real HTTP, with nothing mocked.

Everything in this module is the real thing: a schema built by the real
``create_practice_schema`` (so the RLS policies are the ones that ship),
the real ``DatabaseSessionMiddleware``, the real ``get_patient_context``
dependency, the real ``pablo`` role — which the integration conftest
creates ``NOSUPERUSER NOBYPASSRLS``, the same posture as production — and
real HTTP requests carrying real credentials. No monkeypatching, no
stubbed database, no faked arming. The only stand-in is the resolver
itself, because no real one exists yet; it is the seam under test, not a
mock of the code under test.

**Two route shapes live here, and the difference is the lesson.**

``/patient/record/{patient_id}`` is written the WRONG way on purpose: it
takes an id straight from the URL and drops it in the WHERE clause, which
is exactly the mistake IDOR names. It exists so RLS is the *only* thing
standing between patient A and patient B's record — a route that also
checked ownership would pass these tests even if every policy were
broken, because it would be testing its own ``if`` statement rather than
the database. Isolating the backstop means disconnecting everything in
front of it.

``/patient/me/record`` is the shape real routes should copy. It takes no
id from the client at all and scopes by ``context.patient_id``. That is
better than "check ownership": with no client-supplied id there is no
ownership question to get wrong, and the IDOR surface does not exist to
begin with. The principal already says who the caller is; asking them
again is what creates the hole.

**Do not copy the ``{patient_id}`` routes into production.** They are
adversarial fixtures. The corresponding tests assert they are safe
*anyway* — which is the definition of defense in depth, not a licence to
rely on one layer.

Complements the direct-SQL isolation suite in
``test_patient_principal_rls.py``: same property, asked through the stack
a real attacker would actually reach.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

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
from app.db import get_db_session  # noqa: E402
from app.db.middleware import DatabaseSessionMiddleware  # noqa: E402

_CLINICIAN = "2a7e4c19-5f83-4d6b-8c02-9e1a7b3f5d84"

# Credential -> patient. Two real patients in ONE tenant, which is the
# case tenant-schema isolation cannot help with: same schema, same
# search_path, same connection pool. Only the row policy separates them.
_TOKEN_A = "credential-belonging-to-patient-a"  # noqa: S105 - test credential
_TOKEN_B = "credential-belonging-to-patient-b"  # noqa: S105 - test credential


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[tuple[str, str, str]]:
    """A real provisioned schema with two real patients. Returns (schema, a, b)."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_idor_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        for pid, first, last in ((patient_a, "Ada", "Lovelace"), (patient_b, "Grace", "Hopper")):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, "
                    "session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, "
                    "lower(:first), lower(:last), 'active', 0, now(), now())"
                ),
                {"pid": pid, "first": first, "last": last},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": pid, "u": _CLINICIAN},
            )

    yield schema, patient_a, patient_b

    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class _TwoPatientResolver:
    """Maps two real credentials to two real patients in one real schema."""

    credential_kind = "bearer"

    def __init__(self, schema: str, patient_a: str, patient_b: str) -> None:
        self._by_token = {_TOKEN_A: patient_a, _TOKEN_B: patient_b}
        self._schema = schema

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        patient_id = self._by_token.get(credential.value)
        if patient_id is None:
            return None
        return PatientContext(
            patient_id=patient_id,
            practice_schema=self._schema,
            credential_kind="bearer",
            auth_strength=AuthStrength.STEPPED_UP,
        )


def _build_app(schema: str, patient_a: str, patient_b: str) -> FastAPI:
    app = FastAPI()
    app.add_middleware(DatabaseSessionMiddleware)

    registry = PatientResolverRegistry()
    registry.register(_TwoPatientResolver(schema, patient_a, patient_b))
    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry

    @app.get("/patient/records")
    async def _list(
        _ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, list[str]]:
        rows = get_db_session().execute(text("SELECT id FROM patients")).scalars().all()
        return {"ids": [str(r) for r in rows]}

    @app.get("/patient/record/{patient_id}")
    async def _by_id(
        patient_id: str,
        _ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, list[str]]:
        """NO ownership check, deliberately — RLS is the only thing here."""
        rows = (
            get_db_session()
            .execute(
                text("SELECT first_name FROM patients WHERE id = CAST(:p AS uuid)"),
                {"p": patient_id},
            )
            .scalars()
            .all()
        )
        return {"names": [str(r) for r in rows]}

    @app.get("/patient/me/record")
    async def _me(
        ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, list[str]]:
        """The shape real patient routes should have.

        No id from the client. The principal is the id. There is nothing
        to authorize because there is nothing to disagree about.
        """
        rows = (
            get_db_session()
            .execute(
                text("SELECT first_name FROM patients WHERE id = CAST(:p AS uuid)"),
                {"p": ctx.patient_id},
            )
            .scalars()
            .all()
        )
        return {"names": [str(r) for r in rows]}

    @app.post("/patient/record/{patient_id}/rename")
    async def _rename(
        patient_id: str,
        _ctx: PatientContext = Depends(get_patient_context),
    ) -> dict[str, int]:
        """Also no ownership check. A blind write must miss too."""
        session = get_db_session()
        result = session.execute(
            text("UPDATE patients SET first_name = 'tampered' WHERE id = CAST(:p AS uuid)"),
            {"p": patient_id},
        )
        session.commit()
        return {"rows": result.rowcount}

    return app


@pytest.fixture
def client(tenant: tuple[str, str, str]) -> TestClient:
    return TestClient(_build_app(*tenant))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestIdorOverHttp:
    def test_a_sees_only_their_own_record(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        """Visibility control — without it every assertion below is hollow."""
        _, patient_a, patient_b = tenant
        body = client.get("/patient/records", headers=_auth(_TOKEN_A)).json()
        assert body["ids"] == [patient_a]
        assert patient_b not in body["ids"]

    def test_b_sees_only_their_own_record(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        _, patient_a, patient_b = tenant
        body = client.get("/patient/records", headers=_auth(_TOKEN_B)).json()
        assert body["ids"] == [patient_b]
        assert patient_a not in body["ids"]

    def test_a_requesting_bs_id_in_the_url_gets_nothing(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        """The IDOR move itself: authenticate as A, put B's id in the path."""
        _, patient_a, patient_b = tenant

        # Control: the same endpoint returns A's own record.
        own = client.get(f"/patient/record/{patient_a}", headers=_auth(_TOKEN_A))
        assert own.status_code == 200
        assert own.json()["names"] == ["Ada"]

        # The attack.
        other = client.get(f"/patient/record/{patient_b}", headers=_auth(_TOKEN_A))
        assert other.status_code == 200
        assert other.json()["names"] == [], (
            "patient A read patient B's record by putting B's id in the URL"
        )

    def test_the_attack_works_when_the_credential_is_swapped(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        """Symmetry: B cannot reach A either, so nothing is keyed to A by luck."""
        _, patient_a, _ = tenant
        body = client.get(f"/patient/record/{patient_a}", headers=_auth(_TOKEN_B)).json()
        assert body["names"] == []

    def test_a_cannot_write_to_bs_record_through_the_url(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        _, _, patient_b = tenant

        response = client.post(f"/patient/record/{patient_b}/rename", headers=_auth(_TOKEN_A))
        assert response.status_code == 200
        assert response.json()["rows"] == 0, "patient A wrote to patient B's record"

        # B's record is untouched when B asks.
        body = client.get(f"/patient/record/{patient_b}", headers=_auth(_TOKEN_B)).json()
        assert body["names"] == ["Grace"]

    def test_the_principal_scoped_route_serves_each_patient_their_own(
        self, client: TestClient
    ) -> None:
        """The recommended shape works, and works per-credential.

        Same URL for both patients — no id in it — and each gets only
        their own record. This is the pattern real routes should copy:
        the client never names a patient, so there is no id to tamper
        with and no ownership check to forget.
        """
        assert client.get("/patient/me/record", headers=_auth(_TOKEN_A)).json()["names"] == ["Ada"]
        assert client.get("/patient/me/record", headers=_auth(_TOKEN_B)).json()["names"] == [
            "Grace"
        ]

    def test_the_principal_scoped_route_has_no_id_to_tamper_with(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        """There is no parameter an attacker could swap.

        Belt-and-braces on the design claim: appending B's id as a query
        param changes nothing, because the route never reads one.
        """
        _, _, patient_b = tenant
        body = client.get(
            f"/patient/me/record?patient_id={patient_b}", headers=_auth(_TOKEN_A)
        ).json()
        assert body["names"] == ["Ada"]

    def test_a_forged_credential_gets_401_not_data(self, client: TestClient) -> None:
        assert client.get("/patient/records", headers=_auth("forged")).status_code == 401

    def test_no_credential_gets_401(self, client: TestClient) -> None:
        assert client.get("/patient/records").status_code == 401

    def test_one_patients_request_does_not_bleed_into_the_next(
        self, client: TestClient, tenant: tuple[str, str, str]
    ) -> None:
        """Sequential requests on a shared pool must not carry a principal over.

        A's request, then B's, then A's again — on the same TestClient and
        therefore the same connection pool. If the GUC or the search_path
        survived a request boundary, the middle request would see A's row.
        """
        _, patient_a, patient_b = tenant

        assert client.get("/patient/records", headers=_auth(_TOKEN_A)).json()["ids"] == [patient_a]
        assert client.get("/patient/records", headers=_auth(_TOKEN_B)).json()["ids"] == [patient_b]
        assert client.get("/patient/records", headers=_auth(_TOKEN_A)).json()["ids"] == [patient_a]

    def test_an_unauthenticated_request_between_two_patients_sees_nothing(
        self, client: TestClient
    ) -> None:
        """And the unauthenticated case in the middle of real traffic."""
        client.get("/patient/records", headers=_auth(_TOKEN_A))
        assert client.get("/patient/records").status_code == 401
