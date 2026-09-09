# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the money ledger: constraints, isolation, arithmetic.

``patient_charges`` is the only table in the schema where a wrong row is a
wrong number on somebody's bill, and until this module existed every test of
it ran against in-process fakes. Three separate things were therefore believed
rather than demonstrated, and each is asked here of a real engine, as the real
``pablo`` role — which the integration conftest creates ``NOSUPERUSER
NOBYPASSRLS``, the posture production runs.

**Constraints.** A ``CHECK`` that has only ever been read is a ``CHECK`` you
hope exists. Every one on the table gets a row that violates it and an
assertion that the database refuses it *by name* — asserting on the constraint
name, not merely on "something failed", because a row rejected by the wrong
rule is a rule that is not doing its job. The ``claim_id`` foreign key gets the
same treatment for its *action*: a deleted claim must set the column NULL and
leave the money row standing, and "``SET NULL`` not ``CASCADE``" is a
one-word difference that silently deletes revenue if it is wrong.

**Isolation.** ``GET /api/patients/{id}/charges`` and
``GET /api/patients/{id}/balance`` are asked, over real HTTP, for a client the
caller holds no grant on — in both directions, so nothing passes by being
keyed to one clinician by luck. The answer must be 404 and never 403: a 403
confirms the id exists. Non-vacuous throughout — every "B gets nothing" is
preceded by "A gets it", so an empty ledger cannot pass for isolation.

**Agreement.** The balance the route returns must equal
:func:`app.payments.balance.patient_balance` over the same rows, for a ledger
carrying every kind. Asked twice over: against the rows read back out of band
through the repository, and against the rows the ``/charges`` route itself
serialises — the second catches a field dropped from ``ChargeResponse``, which
would make the ledger the practice reads and the total it is shown disagree
without either one looking wrong. Agreement alone is not enough, though: two
computations sharing one wrong rule agree perfectly, so the absolute cents are
asserted too, and a paid bill has to net to zero rather than to a refund the
practice owes.

Deliberately NOT re-tested here: that ``patient_charges`` is RLS-forced and
fail-closed with no GUC set. ``test_rls_invariants.py`` asserts that
generically for every tenant table, and duplicating it would rot.

Run: ``make test-integration``.
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

# Imported at module scope, not inside the app builder, on purpose: this module
# runs under ``from __future__ import annotations``, so FastAPI resolves a
# dependency's annotations against the MODULE globals. A ``Request`` visible
# only inside the builder's local scope leaves ``request: Request`` unresolved,
# and FastAPI falls back to treating it as a required query parameter — every
# call then answers 422 instead of running. Nothing here imports ``app.*``,
# which is the rule this directory's conftest actually cares about.
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

# Both must be set before the first ``app.settings`` read, which is
# ``lru_cache``'d — same reason and same placement as the api/ e2e modules.
# Multi-tenancy is what makes ``DatabaseSessionMiddleware`` honour the
# per-request schema resolver; with it off the middleware pins every session
# to the ``practice`` template and the routes below would read an empty
# ledger out of the wrong schema.
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("MULTI_TENANCY_ENABLED", "true")

_CLINICIAN_A = "3c5f2c0a-8e2d-5b9f-9e8d-8e4f7c3e3c03"
_CLINICIAN_B = "4d6a3d1b-9f3e-5cab-af9e-9f5a8d4f4d04"

#: Which clinician a request speaks for. Stands in for the bearer token the
#: real auth chain would decode; the seam it replaces is the auth layer, not
#: anything under test.
_PRINCIPAL_HEADER = "X-Test-Clinician"

# Deliberately not shaped like real credentials, the same posture as
# ``tests/test_patient_payments.py``: nothing here ever parses these, no Stripe
# call is made, and a fixture imitating a credential would be indistinguishable
# from a leaked one to a secret scanner.
_SECRET_KEY = "secret-key-for-tests"  # noqa: S105 - a placeholder, not a credential
_PUBLISHABLE_KEY = "publishable-key-for-tests"

# A local copy of ``app.db.models.CHARGE_KINDS``, because parametrisation is
# evaluated at import time and this directory's conftest forbids importing
# ``app.*`` at module scope. ``test_the_kind_list_here_matches_the_models``
# fails loudly if the two ever drift, which is what keeps a newly added kind
# from quietly going untested here.
_CHARGE_KINDS = (
    "session",
    "copay",
    "payment",
    "patient_resp",
    "contractual_adjustment",
    "write_off",
    "credit",
)

_INSERT_CHARGE = text(
    "INSERT INTO patient_charges "
    "(id, patient_id, appointment_id, kind, claim_id, write_off_reason, note, "
    " settled_by_charge_id, amount_cents, currency, status, created_by_user_id, created_at) "
    "VALUES (:id, CAST(:patient_id AS uuid), NULL, :kind, CAST(:claim_id AS uuid), "
    " :write_off_reason, NULL, NULL, :amount_cents, 'usd', :status, :user_id, now())"
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A schema built by the real provisioning path, so the DDL is the shipped DDL."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_charges_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _seed_patient(engine: Engine, schema: str, clinician_id: str, last_name: str) -> str:
    """One client in ``schema``, granted to exactly one clinician."""
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"), {"u": clinician_id}
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', :last, 'test', lower(:last), "
                "'active', 0, now(), now())"
            ),
            {"pid": pid, "last": last_name},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": pid, "u": clinician_id},
        )
    return pid


@pytest.fixture(scope="module")
def patient_a(engine: Engine, tenant_schema: str) -> str:
    """A client only clinician A holds a grant on."""
    return _seed_patient(engine, tenant_schema, _CLINICIAN_A, "Ledgerson")


@pytest.fixture(scope="module")
def patient_b(engine: Engine, tenant_schema: str) -> str:
    """A client only clinician B holds a grant on, in the SAME schema.

    Same schema on purpose: schema separation cannot help here, so the row
    policy is the only thing keeping these two clients apart.
    """
    return _seed_patient(engine, tenant_schema, _CLINICIAN_B, "Balanceford")


def _armed(engine: Engine, schema: str, user_id: str) -> Connection:
    """A connection in ``schema`` with the RLS principal armed. Caller closes it."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


@pytest.fixture
def armed_conn(engine: Engine, tenant_schema: str) -> Iterator[Connection]:
    """Clinician A's connection, rolled back at the end so tests stay independent."""
    conn = _armed(engine, tenant_schema, _CLINICIAN_A)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _charge_params(patient_id: str, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "patient_id": patient_id,
        "kind": "session",
        "claim_id": None,
        "write_off_reason": None,
        "amount_cents": 15000,
        "status": "pending",
        "user_id": _CLINICIAN_A,
    }
    params.update(overrides)
    return params


class TestCheckConstraints:
    """Every ``CHECK`` on the table, executed rather than read."""

    def test_the_kind_list_here_matches_the_models(self) -> None:
        """Drift guard for the local copy every parametrised case below reads.

        A kind added to the models and not to this tuple would silently go
        untested by the whole module — the amount rule, the write-off
        biconditional and the every-kind ledger all iterate this list.
        """
        from app.db.models import CHARGE_KINDS  # noqa: PLC0415

        assert _CHARGE_KINDS == CHARGE_KINDS

    def test_every_kind_is_accepted_by_the_constraint(
        self, armed_conn: Connection, patient_a: str
    ) -> None:
        """The whole allowed set, so the rule is a list and not a shorter one."""
        for kind in _CHARGE_KINDS:
            reason = "hardship" if kind == "write_off" else None
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind=kind, write_off_reason=reason),
            )

    def test_a_well_formed_row_is_accepted(self, armed_conn: Connection, patient_a: str) -> None:
        """Control. Without it every rejection below could be rejecting everything."""
        params = _charge_params(patient_a)
        armed_conn.execute(_INSERT_CHARGE, params)
        stored = armed_conn.execute(
            text("SELECT kind, amount_cents, status FROM patient_charges WHERE id = :id"),
            {"id": params["id"]},
        ).one()
        assert stored == ("session", 15000, "pending")

    def test_an_unknown_kind_is_rejected(self, armed_conn: Connection, patient_a: str) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(_INSERT_CHARGE, _charge_params(patient_a, kind="refund"))
        assert "ck_patient_charges_kind" in str(exc.value)

    def test_an_unknown_status_is_rejected(self, armed_conn: Connection, patient_a: str) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(_INSERT_CHARGE, _charge_params(patient_a, status="settled"))
        assert "ck_patient_charges_status" in str(exc.value)

    def test_a_write_off_without_a_reason_is_rejected(
        self, armed_conn: Connection, patient_a: str
    ) -> None:
        """An unexplained write-off is the row nobody can account for later."""
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind="write_off", write_off_reason=None),
            )
        assert "ck_patient_charges_write_off_reason_kind" in str(exc.value)

    @pytest.mark.parametrize("kind", [k for k in _CHARGE_KINDS if k != "write_off"])
    def test_a_reason_on_anything_but_a_write_off_is_rejected(
        self, armed_conn: Connection, patient_a: str, kind: str
    ) -> None:
        """The other half of the biconditional: a reason on a copay means the kind is wrong.

        Parametrised over every non-write-off kind because the constraint is
        written as an equality between two booleans, and an equality that held
        for only some kinds would still read correctly.
        """
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind=kind, write_off_reason="hardship"),
            )
        assert "ck_patient_charges_write_off_reason_kind" in str(exc.value)

    def test_a_write_off_with_a_reason_is_accepted(
        self, armed_conn: Connection, patient_a: str
    ) -> None:
        """The accepted corner of the biconditional, so it is not simply deny-all."""
        params = _charge_params(patient_a, kind="write_off", write_off_reason="small_balance")
        armed_conn.execute(_INSERT_CHARGE, params)
        stored = armed_conn.execute(
            text("SELECT kind, write_off_reason FROM patient_charges WHERE id = :id"),
            {"id": params["id"]},
        ).one()
        assert stored == ("write_off", "small_balance")

    def test_an_unknown_write_off_reason_is_rejected(
        self, armed_conn: Connection, patient_a: str
    ) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind="write_off", write_off_reason="felt_like_it"),
            )
        assert "ck_patient_charges_write_off_reason" in str(exc.value)

    @pytest.mark.parametrize("amount", [0, -1, -15000])
    @pytest.mark.parametrize("kind", _CHARGE_KINDS)
    def test_a_non_positive_amount_is_rejected_for_every_kind(
        self, armed_conn: Connection, patient_a: str, kind: str, amount: int
    ) -> None:
        """Every kind, including the two the balance arithmetic subtracts.

        ``contractual_adjustment`` and ``credit`` reduce a balance, which makes
        "store it as a negative number" the obvious mistake — and the one that
        would double the reduction, because the sign is applied again by kind
        in ``patient_balance``. The table has to refuse them as flatly as it
        refuses a negative session charge.
        """
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        reason = "hardship" if kind == "write_off" else None
        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind=kind, write_off_reason=reason, amount_cents=amount),
            )
        assert "ck_patient_charges_amount_positive" in str(exc.value)


@pytest.fixture(scope="module")
def claim_id(engine: Engine, tenant_schema: str, patient_a: str) -> str:
    """A real claim for client A, built through the real claims repository."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.models.coverage import PatientCoverage  # noqa: PLC0415
    from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from app.services.coverage_intake import new_payer  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415
    from tests.claims_fixtures import claim, line  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(_CLINICIAN_A)
    session = OrmSession(bind=engine)
    try:
        session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        arm_current_user_id(session, _CLINICIAN_A)

        payer = PostgresPayerRepository(session).create(
            new_payer(name="Ledger Test Payer", payer_id="LEDGER")
        )
        now = datetime.now(UTC)
        coverage = PostgresPatientCoverageRepository(session).create(
            PatientCoverage(
                id=str(uuid.uuid4()),
                patient_id=patient_a,
                payer_id=payer.id,
                member_id="LEDGER-1",
                created_at=now,
                updated_at=now,
            )
        )
        new_id = str(uuid.uuid4())
        control = uuid.uuid4().hex[:12].upper()
        created = PostgresClaimRepository(session).create(
            claim(
                id=new_id,
                control_number=control,
                patient_id=patient_a,
                coverage_id=coverage.id,
                payer_id=payer.id,
                created_at=now,
                updated_at=now,
                lines=[
                    line(
                        id=str(uuid.uuid4()),
                        claim_id=new_id,
                        patient_id=patient_a,
                        line_control_number=f"{control}L1",
                        created_at=now,
                    )
                ],
            )
        )
        session.commit()
        return created.id
    finally:
        session.close()
        _current_tenant_schema.reset(schema_token)
        _current_user_id.reset(uid_token)


class TestClaimForeignKey:
    """``claim_id`` points at a real claim, and a deleted claim never takes money with it."""

    def test_a_claim_id_that_names_no_claim_is_rejected(
        self, armed_conn: Connection, patient_a: str
    ) -> None:
        """Proves the constraint is a foreign key at all, not a bare uuid column."""
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        with pytest.raises(IntegrityError) as exc:
            armed_conn.execute(
                _INSERT_CHARGE,
                _charge_params(patient_a, kind="patient_resp", claim_id=str(uuid.uuid4())),
            )
        assert "fk_patient_charges_claim_id_claims" in str(exc.value)

    def test_deleting_the_claim_nulls_the_link_and_keeps_the_money_row(
        self, engine: Engine, tenant_schema: str, patient_a: str, claim_id: str
    ) -> None:
        """``SET NULL``, not ``CASCADE``.

        The one-word difference between "this row no longer belongs to a
        claim" and "this row is gone", on the table that says what a client
        was billed. Asserted against a real ``DELETE`` because the difference
        is invisible in the model definition until something deletes a claim.
        """
        charge_id = uuid.uuid4().hex
        conn = _armed(engine, tenant_schema, _CLINICIAN_A)
        try:
            conn.execute(
                _INSERT_CHARGE,
                _charge_params(
                    patient_a,
                    id=charge_id,
                    kind="patient_resp",
                    claim_id=claim_id,
                    amount_cents=4000,
                ),
            )
            conn.commit()

            linked = conn.execute(
                text("SELECT claim_id FROM patient_charges WHERE id = :id"), {"id": charge_id}
            ).scalar_one()
            assert str(linked) == claim_id, "Control: the row must start out linked to the claim"

            conn.execute(text("DELETE FROM claims WHERE id = CAST(:c AS uuid)"), {"c": claim_id})
            conn.commit()

            survivor = conn.execute(
                text("SELECT claim_id, amount_cents, kind FROM patient_charges WHERE id = :id"),
                {"id": charge_id},
            ).one_or_none()
            assert survivor is not None, (
                "Deleting a claim deleted the money row — the foreign key is CASCADE, not SET NULL"
            )
            assert survivor == (None, 4000, "patient_resp")
        finally:
            conn.execute(text("DELETE FROM patient_charges WHERE id = :id"), {"id": charge_id})
            conn.commit()
            conn.close()


def _user(user_id: str, email: str) -> Any:
    from app.models import User  # noqa: PLC0415

    accepted = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id,
        email=email,
        name="Integration Clinician",
        created_at=accepted,
        baa_accepted_at=accepted,
        baa_version="2026-01-01",
    )


class _AlwaysConfigured:
    """A payment credential provider, so the ledger route is not gated on 503.

    ``GET /charges`` refuses with 503 when the deployment has no card
    processing configured, and that check runs before the client is looked up
    — so without this the isolation assertions below would be asserting on the
    wrong refusal.
    """

    def credentials_for_practice(self, practice_id: str | None) -> Any:  # noqa: ARG002
        from app.payments.provider import PaymentCredentials  # noqa: PLC0415

        return PaymentCredentials(secret_key=_SECRET_KEY, publishable_key=_PUBLISHABLE_KEY)


def _build_app(tenant_schema: str) -> FastAPI:
    """The real payments router, real middleware, two real credentials."""
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_tenant_context,
        require_baa_acceptance,
    )
    from app.db import arm_current_user_id, get_db_session  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415
    from app.routes.patient_payments import router  # noqa: PLC0415

    users = {
        _CLINICIAN_A: _user(_CLINICIAN_A, "clinician-a@example.com"),
        _CLINICIAN_B: _user(_CLINICIAN_B, "clinician-b@example.com"),
    }

    app = FastAPI()
    app.add_middleware(DatabaseSessionMiddleware)
    app.include_router(router)

    def _principal(request: Request) -> Any:
        """Stand in for the auth chain — including the part that arms RLS.

        The real chain arms ``app.current_user_id`` in ``_resolve_user``, on
        every authenticated request, which is what lets a route reach tenant
        rows at all under a NOBYPASSRLS role. An override that skipped it
        would make every route below read zero rows and every isolation
        assertion pass vacuously.
        """
        user = users.get(request.headers.get(_PRINCIPAL_HEADER, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="No principal.")
        arm_current_user_id(get_db_session(), user.id)
        return user

    def _tenant(request: Request) -> TenantContext:
        user = _principal(request)
        return TenantContext(
            user_id=user.id,
            practice_id="integration-practice",
            practice_schema=tenant_schema,
        )

    app.dependency_overrides[require_baa_acceptance] = _principal
    app.dependency_overrides[get_tenant_context] = _tenant
    return app


@pytest.fixture
def client(tenant_schema: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.payments.provider import register_payment_credential_provider  # noqa: PLC0415

    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request",
        lambda _request: (tenant_schema, "resolved"),
    )
    register_payment_credential_provider(_AlwaysConfigured())
    try:
        yield TestClient(_build_app(tenant_schema))
    finally:
        register_payment_credential_provider(None)


def _as(clinician_id: str) -> dict[str, str]:
    return {_PRINCIPAL_HEADER: clinician_id}


@pytest.fixture
def seeded_ledger(engine: Engine, tenant_schema: str, patient_a: str) -> Iterator[dict[str, int]]:
    """One row of every kind on client A's ledger, written by the real repository.

    Yields the amounts by kind so the assertions can name figures rather than
    magic numbers, and clears the ledger afterwards so the isolation tests
    still see a table they seeded themselves.
    """
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.patient_payment import (  # noqa: PLC0415
        PostgresPatientPaymentRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    visit_one = str(uuid.uuid4())
    visit_two = str(uuid.uuid4())
    amounts = {
        "session_unpaid": 15000,
        "session_paid": 12000,
        "copay": 2500,
        "patient_resp": 4000,
        "payment": 4000,
        "contractual_adjustment": 3000,
        "write_off": 5000,
        "credit": 2000,
    }

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(_CLINICIAN_A)
    session = OrmSession(bind=engine)
    try:
        session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        arm_current_user_id(session, _CLINICIAN_A)
        repo = PostgresPatientPaymentRepository(session)

        repo.stage_charge(
            patient_id=patient_a,
            appointment_id=visit_one,
            amount_cents=amounts["session_unpaid"],
            currency="usd",
            user_id=_CLINICIAN_A,
        )
        paid = repo.stage_charge(
            patient_id=patient_a,
            appointment_id=visit_two,
            amount_cents=amounts["session_paid"],
            currency="usd",
            user_id=_CLINICIAN_A,
        )
        repo.commit()
        repo.close_charge(paid.id, status="succeeded", status_detail=None)

        repo.add_ledger_row(
            patient_id=patient_a,
            kind="copay",
            amount_cents=amounts["copay"],
            currency="usd",
            user_id=_CLINICIAN_A,
            appointment_id=visit_two,
        )
        responsibility = repo.add_ledger_row(
            patient_id=patient_a,
            kind="patient_resp",
            amount_cents=amounts["patient_resp"],
            currency="usd",
            user_id=_CLINICIAN_A,
            appointment_id=visit_two,
        )
        # The kind that exists so money can be collected against a bill some
        # other row raised — and the settlement link that records which bill
        # it paid. Both are in the fixture because both are exactly what the
        # arithmetic has to handle without double-counting.
        settling_payment = repo.add_ledger_row(
            patient_id=patient_a,
            kind="payment",
            amount_cents=amounts["payment"],
            currency="usd",
            user_id=_CLINICIAN_A,
            appointment_id=visit_two,
        )
        repo.record_settlement(responsibility.id, settled_by_charge_id=settling_payment.id)
        repo.add_ledger_row(
            patient_id=patient_a,
            kind="contractual_adjustment",
            amount_cents=amounts["contractual_adjustment"],
            currency="usd",
            user_id=_CLINICIAN_A,
            appointment_id=visit_two,
        )
        repo.add_ledger_row(
            patient_id=patient_a,
            kind="write_off",
            amount_cents=amounts["write_off"],
            currency="usd",
            user_id=_CLINICIAN_A,
            appointment_id=visit_one,
            write_off_reason="hardship",
        )
        repo.add_ledger_row(
            patient_id=patient_a,
            kind="credit",
            amount_cents=amounts["credit"],
            currency="usd",
            user_id=_CLINICIAN_A,
        )
        yield amounts
    finally:
        session.rollback()
        session.execute(
            text("DELETE FROM patient_charges WHERE patient_id = CAST(:p AS uuid)"),
            {"p": patient_a},
        )
        session.commit()
        session.close()
        _current_tenant_schema.reset(schema_token)
        _current_user_id.reset(uid_token)


def _charges_from_db(engine: Engine, tenant_schema: str, patient_id: str) -> list[Any]:
    """The ledger read out of band through the repository, as the route reads it."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.patient_payment import (  # noqa: PLC0415
        PostgresPatientPaymentRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(_CLINICIAN_A)
    session = OrmSession(bind=engine)
    try:
        session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        arm_current_user_id(session, _CLINICIAN_A)
        return PostgresPatientPaymentRepository(session).list_charges(patient_id)
    finally:
        session.close()
        _current_tenant_schema.reset(schema_token)
        _current_user_id.reset(uid_token)


def _summary_as_body(summary: Any) -> dict[str, Any]:
    """A :class:`BalanceSummary` in the shape ``BalanceResponse`` serialises to."""
    return {
        "owed_cents": summary.owed_cents,
        "collected_cents": summary.collected_cents,
        "written_off_cents": summary.written_off_cents,
        "adjusted_cents": summary.adjusted_cents,
        "credited_cents": summary.credited_cents,
        "balance_cents": summary.balance_cents,
        "by_visit": [
            {
                "appointment_id": visit.appointment_id,
                "owed_cents": visit.owed_cents,
                "collected_cents": visit.collected_cents,
                "written_off_cents": visit.written_off_cents,
                "adjusted_cents": visit.adjusted_cents,
                "credited_cents": visit.credited_cents,
                "balance_cents": visit.balance_cents,
            }
            for visit in summary.by_visit
        ],
    }


@pytest.mark.usefixtures("seeded_ledger")
class TestCrossClinicianIsolation:
    """Neither clinician can reach the other's client, in either direction."""

    def test_a_reads_their_own_ledger_and_balance(self, client: TestClient, patient_a: str) -> None:
        """Control. Every 404 below is only meaningful beside these 200s."""
        charges = client.get(f"/api/patients/{patient_a}/charges", headers=_as(_CLINICIAN_A))
        assert charges.status_code == 200, charges.text
        assert len(charges.json()) == len(_CHARGE_KINDS) + 1

        balance = client.get(f"/api/patients/{patient_a}/balance", headers=_as(_CLINICIAN_A))
        assert balance.status_code == 200, balance.text

    def test_b_reads_their_own_client(self, client: TestClient, patient_b: str) -> None:
        """The other control: B is a real principal with a real client of their own."""
        charges = client.get(f"/api/patients/{patient_b}/charges", headers=_as(_CLINICIAN_B))
        assert charges.status_code == 200, charges.text
        assert charges.json() == []

        balance = client.get(f"/api/patients/{patient_b}/balance", headers=_as(_CLINICIAN_B))
        assert balance.status_code == 200, balance.text
        assert balance.json()["balance_cents"] == 0

    @pytest.mark.parametrize("route", ["charges", "balance"])
    def test_b_cannot_reach_as_ledger(self, client: TestClient, patient_a: str, route: str) -> None:
        response = client.get(f"/api/patients/{patient_a}/{route}", headers=_as(_CLINICIAN_B))
        assert response.status_code == 404, (
            f"clinician B read clinician A's client through /{route}: {response.text}"
        )

    @pytest.mark.parametrize("route", ["charges", "balance"])
    def test_a_cannot_reach_bs_ledger(self, client: TestClient, patient_b: str, route: str) -> None:
        """The symmetric case, so nothing above passed by being keyed to A."""
        response = client.get(f"/api/patients/{patient_b}/{route}", headers=_as(_CLINICIAN_A))
        assert response.status_code == 404, (
            f"clinician A read clinician B's client through /{route}: {response.text}"
        )

    @pytest.mark.parametrize("route", ["charges", "balance"])
    def test_refusal_is_404_and_never_403(
        self, client: TestClient, patient_a: str, route: str
    ) -> None:
        """A 403 would confirm the id names a real client in this practice."""
        response = client.get(f"/api/patients/{patient_a}/{route}", headers=_as(_CLINICIAN_B))
        assert response.status_code != 403
        unknown = client.get(f"/api/patients/{uuid.uuid4()}/{route}", headers=_as(_CLINICIAN_B))
        assert unknown.status_code == response.status_code, (
            "a client that exists and one that does not must be indistinguishable"
        )

    def test_the_row_policy_hides_the_rows_themselves(
        self, engine: Engine, tenant_schema: str, patient_a: str
    ) -> None:
        """Below the route: B's own session counts zero of A's ledger rows.

        The routes refuse first, so without this the 404s above would only
        prove the route's ``_require_patient`` check works — not that the
        table would hold if a future route forgot it.
        """
        conn = _armed(engine, tenant_schema, _CLINICIAN_A)
        try:
            control = conn.execute(
                text("SELECT count(*) FROM patient_charges WHERE patient_id = CAST(:p AS uuid)"),
                {"p": patient_a},
            ).scalar_one()
            assert control > 0, "Control: A must count their own client's rows"
        finally:
            conn.close()

        conn = _armed(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM patient_charges WHERE patient_id = CAST(:p AS uuid)"
                    ),
                    {"p": patient_a},
                ).scalar_one()
                == 0
            )
        finally:
            conn.close()


class TestRouteAgreesWithTheBalanceFunction:
    """The round trip and the pure function must not disagree."""

    def test_the_route_matches_the_function_over_the_stored_rows(
        self,
        client: TestClient,
        engine: Engine,
        tenant_schema: str,
        patient_a: str,
        seeded_ledger: dict[str, int],
    ) -> None:
        from app.payments.balance import patient_balance  # noqa: PLC0415

        response = client.get(f"/api/patients/{patient_a}/balance", headers=_as(_CLINICIAN_A))
        assert response.status_code == 200, response.text

        expected = patient_balance(_charges_from_db(engine, tenant_schema, patient_a))
        assert response.json() == _summary_as_body(expected)

        # Non-vacuous: a ledger that summed to nothing would match anything.
        assert expected.written_off_cents == seeded_ledger["write_off"]
        assert expected.adjusted_cents == seeded_ledger["contractual_adjustment"]
        assert expected.credited_cents == seeded_ledger["credit"]

    def test_a_paid_bill_nets_to_zero_over_a_real_round_trip(
        self, client: TestClient, patient_a: str, seeded_ledger: dict[str, int]
    ) -> None:
        """The figures themselves, not merely that two computations agree.

        A bill stays a bill and payment cancels it, so the paid session is on
        both sides and nets to nothing, and the ``payment`` row that settled
        the ``patient_resp`` cancels that bill without erasing it. Asserted
        here as absolute cents because "the route agrees with the function" is
        satisfied by any arithmetic they share — including arithmetic that
        drops a paid bill from the owed side and reads a settled account as a
        refund the practice owes.
        """
        body = client.get(f"/api/patients/{patient_a}/balance", headers=_as(_CLINICIAN_A)).json()

        billed = (
            seeded_ledger["session_unpaid"]
            + seeded_ledger["session_paid"]
            + seeded_ledger["patient_resp"]
        )
        paid = seeded_ledger["session_paid"] + seeded_ledger["copay"] + seeded_ledger["payment"]
        assert body["owed_cents"] == billed
        assert body["collected_cents"] == paid
        assert body["balance_cents"] == (
            billed - paid - seeded_ledger["write_off"] - seeded_ledger["credit"]
        )
        assert body["balance_cents"] > 0, (
            "a client who has paid for what they were billed must not read as owed a refund"
        )

    @pytest.mark.usefixtures("seeded_ledger")
    def test_the_ledger_the_route_serialises_totals_to_the_same_balance(
        self, client: TestClient, patient_a: str
    ) -> None:
        """``/charges`` must carry every field the arithmetic reads.

        The practice sees the ledger through ``/charges`` and the total
        through ``/balance``. If ``ChargeResponse`` dropped ``kind``, or
        ``settled_by_charge_id``, the two would tell different stories and
        neither response would look wrong on its own.
        """
        from app.models.payments import PatientCharge  # noqa: PLC0415
        from app.payments.balance import patient_balance  # noqa: PLC0415

        listed = client.get(f"/api/patients/{patient_a}/charges", headers=_as(_CLINICIAN_A))
        assert listed.status_code == 200, listed.text
        rows = [
            PatientCharge(patient_id=patient_a, created_by_user_id=_CLINICIAN_A, **row)
            for row in listed.json()
        ]
        assert {row.kind for row in rows} == set(_CHARGE_KINDS), (
            "the seeded ledger must exercise every kind"
        )

        balance = client.get(f"/api/patients/{patient_a}/balance", headers=_as(_CLINICIAN_A))
        assert balance.json() == _summary_as_body(patient_balance(rows))
