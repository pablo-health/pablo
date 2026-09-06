# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the claims pipeline: provisioning, isolation, idempotency.

A fresh tenant provisioned from the template carries ``claim_events``,
force-RLS'd with the ``has_patient_access`` policy like the claims it
belongs to. The outbox then runs the way the scheduled job runs it — a
tenant session armed as the claim's owner, the clearinghouse answered
from recorded fixtures, the default event listener in place — and proves
what SQLite cannot: the rejection a worker raises lands as a compliance
reminder under that clinician's row policy, visible to them and to nobody
else; the pending marker written before the submission call survives a
crash and is replayed with the same key; and the two uniqueness rules the
acknowledgement paths rely on are enforced by the database, not only by
the code that checks first. Real NOSUPERUSER NOBYPASSRLS role (see
conftest.py); every "B sees nothing" follows an "A sees the row".

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN_A = "1a3d0a8e-6f0c-5f7e-9c6b-6c2f5a1c1a01"
_CLINICIAN_B = "2b4e1b9f-7a1d-5a8f-8d7c-7d3a6b2d2b02"


class _KilledError(RuntimeError):
    """The process died after the request left and before the answer was written."""


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
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_claim_pipe_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_id(engine: Engine, tenant_schema: str) -> str:
    """A client granted to clinician A only."""
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN_A}
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', 'test', 'patient', "
                "'active', 0, now(), now())"
            ),
            {"pid": pid},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": pid, "u": _CLINICIAN_A},
        )
    return pid


class _TenantSession:
    """A tenant session armed as one clinician, the way a worker opens one."""

    def __init__(self, engine: Engine, schema: str, user_id: str) -> None:
        from app.db import (  # noqa: PLC0415
            _current_tenant_schema,
            _current_user_id,
            arm_current_user_id,
        )
        from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

        self._schema_token = _current_tenant_schema.set(schema)
        self._uid_token = _current_user_id.set(user_id)
        self.session = OrmSession(bind=engine)
        self.session.execute(text(f"SET search_path = {schema}, platform, public"))
        arm_current_user_id(self.session, user_id)

    def close(self) -> None:
        from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

        self.session.close()
        _current_tenant_schema.reset(self._schema_token)
        _current_user_id.reset(self._uid_token)


@pytest.fixture(scope="module")
def coverage_ids(engine: Engine, tenant_schema: str, patient_id: str) -> tuple[str, str]:
    """A payer and an active coverage for the client, as clinician A. (coverage_id, payer_id)"""
    from app.models.coverage import PatientCoverage  # noqa: PLC0415
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from app.services.coverage_intake import new_payer  # noqa: PLC0415

    scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer = PostgresPayerRepository(scoped.session).create(
            new_payer(name="Stedi Test Payer", payer_id="STEDI")
        )
        now = datetime.now(UTC)
        coverage = PostgresPatientCoverageRepository(scoped.session).create(
            PatientCoverage(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                payer_id=payer.id,
                member_id="123456789",
                created_at=now,
                updated_at=now,
            )
        )
        scoped.session.commit()
        return coverage.id, payer.id
    finally:
        scoped.close()


def _pipeline(scoped: _TenantSession, user_id: str) -> Any:
    from app.claims.receipts import ClaimPipeline  # noqa: PLC0415
    from app.repositories.postgres.claim_receipts import (  # noqa: PLC0415
        PostgresClaimReceiptRepository,
    )
    from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415

    return ClaimPipeline(
        claims=PostgresClaimRepository(scoped.session),
        receipts=PostgresClaimReceiptRepository(scoped.session),
        session=scoped.session,
        principal_user_id=user_id,
    )


def _validated_claim(
    scoped: _TenantSession, patient_id: str, coverage_ids: tuple[str, str], **overrides: Any
) -> Any:
    """A validated claim owned by clinician A, committed."""
    from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415
    from tests.claims_fixtures import billing_snapshot, claim, line  # noqa: PLC0415
    from tests.claims_pipeline_fakes import fresh_control_number  # noqa: PLC0415

    coverage_id, payer_id = coverage_ids
    claim_id = str(uuid.uuid4())
    control = fresh_control_number()
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": claim_id,
        "control_number": control,
        "patient_id": patient_id,
        "coverage_id": coverage_id,
        "payer_id": payer_id,
        "state": "validated",
        "billing_snapshot": billing_snapshot(user_id=_CLINICIAN_A),
        "created_at": now,
        "updated_at": now,
        "lines": [
            line(
                id=str(uuid.uuid4()),
                claim_id=claim_id,
                patient_id=patient_id,
                line_control_number=f"{control}L1",
                created_at=now,
            )
        ],
    }
    fields.update(overrides)
    created = PostgresClaimRepository(scoped.session).create(claim(**fields))
    scoped.session.commit()
    return created


def _submit(scoped: _TenantSession, client: Any) -> Any:
    from app.claims.submit_worker import submit_pending  # noqa: PLC0415
    from app.repositories.postgres.coverage import PostgresPayerRepository  # noqa: PLC0415
    from tests.claims_pipeline_fakes import ACCOUNT  # noqa: PLC0415

    return submit_pending(
        _pipeline(scoped, _CLINICIAN_A),
        client,
        ACCOUNT,
        payers=PostgresPayerRepository(scoped.session),
        practice_user_ids=[_CLINICIAN_A, _CLINICIAN_B],
        commit=scoped.session.commit,
    )


def _reminders_visible_to(engine: Engine, schema: str, user_id: str) -> list[Any]:
    from app.db.models import ComplianceItemRow  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    scoped = _TenantSession(engine, schema, user_id)
    try:
        return list(scoped.session.execute(select(ComplianceItemRow)).scalars().all())
    finally:
        scoped.close()


class TestProvisioning:
    def test_fresh_tenant_has_the_receipt_ledger_and_the_new_claim_columns(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            tables = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = 'claim_events'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert list(tables) == ["claim_events"]
            columns = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'claims' "
                    "AND column_name IN ('vendor_claim_id', 'payer_claim_number', "
                    "'submission_idempotency_key', 'submission_pending_at', "
                    "'submission_findings', 'last_receipt_at', 'status_checked_at')"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert len(set(columns)) == 7

    def test_the_ledger_is_forced_rls_with_the_patient_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            [(rls, forced)] = conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'claim_events'"
                ),
                {"s": tenant_schema},
            ).all()
            assert (rls, forced) == (True, True)
            policies = conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = 'claim_events'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert "rls_patient_access" in set(policies)

    def test_the_two_uniqueness_rules_exist(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            constraints = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_schema = :s AND table_name = 'claim_events' "
                    "AND constraint_type = 'UNIQUE'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert {"ux_claim_events_vendor_event_id", "ux_claim_events_deadline_rung"} <= set(
                constraints
            )


class TestOutboxOnPostgres:
    def test_a_rejection_lands_as_the_owners_reminder_and_nobody_elses(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from tests.claims_pipeline_fakes import (  # noqa: PLC0415
            FakeClearinghouse,
            submission_edit_rejected,
        )

        client = FakeClearinghouse()
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            created = _validated_claim(scoped, patient_id, coverage_ids)
            client.answers.append(submission_edit_rejected(created.control_number))
            summary = _submit(scoped, client)
            scoped.session.commit()

            assert summary.rejected == 1
            pipeline = _pipeline(scoped, _CLINICIAN_A)
            saved = pipeline.claims.get(created.id)
            assert saved is not None
            assert saved.state == "rejected"
            assert [f.code for f in saved.submission_findings] == ["33"]
            [receipt] = pipeline.receipts.list_for_claim(created.id)
            assert receipt.kind == "rejected"
        finally:
            scoped.close()

        mine = [
            r
            for r in _reminders_visible_to(engine, tenant_schema, _CLINICIAN_A)
            if r.item_type == "claim_rejected" and created.control_number in (r.notes or "")
        ]
        assert len(mine) == 1, "Control: the owner sees the reminder the worker wrote"
        assert mine[0].user_id == _CLINICIAN_A
        for forbidden in ("123456789", "Anon", "F41", "2000-01-01"):
            assert forbidden not in mine[0].label
        assert _reminders_visible_to(engine, tenant_schema, _CLINICIAN_B) == []

    def test_clinician_b_sees_no_receipt(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        from app.repositories.postgres.claim_receipts import (  # noqa: PLC0415
            PostgresClaimReceiptRepository,
        )
        from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415

        scoped_a = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            claims_a = PostgresClaimRepository(scoped_a.session).list_by_patient(patient_id)
            with_receipts = [
                c
                for c in claims_a
                if PostgresClaimReceiptRepository(scoped_a.session).list_for_claim(c.id)
            ]
            assert with_receipts, "Control: A sees a claim with receipts from the test above"
            claim_id = with_receipts[0].id
        finally:
            scoped_a.close()

        scoped_b = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert PostgresClaimRepository(scoped_b.session).get(claim_id) is None
            assert PostgresClaimReceiptRepository(scoped_b.session).list_for_claim(claim_id) == []
        finally:
            scoped_b.close()

    def test_a_crash_after_the_call_is_replayed_with_the_committed_key(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from tests.claims_pipeline_fakes import FakeClearinghouse  # noqa: PLC0415

        client = FakeClearinghouse()
        client.answers.append(_KilledError("SIGKILL"))
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            created = _validated_claim(scoped, patient_id, coverage_ids)
            with pytest.raises(_KilledError):
                _submit(scoped, client)
            scoped.session.rollback()
        finally:
            scoped.close()

        # A brand-new session, as a restarted worker would open: the marker
        # was committed before the call, so it is there to reconcile.
        restarted = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            pipeline = _pipeline(restarted, _CLINICIAN_A)
            pending = pipeline.claims.get(created.id)
            assert pending is not None
            assert pending.state == "validated"
            assert pending.submission_pending_at is not None
            stored_key = pending.submission_idempotency_key
            assert stored_key is not None

            summary = _submit(restarted, client)
            restarted.session.commit()

            assert summary.submitted == 1
            saved = pipeline.claims.get(created.id)
            assert saved is not None
            assert saved.state == "submitted"
            assert saved.submission_pending_at is None
            assert [key for _req, key in client.submissions] == [stored_key, stored_key]
        finally:
            restarted.close()


class TestAcknowledgementsOnPostgres:
    def test_a_redelivered_event_moves_nothing_and_the_database_refuses_a_second_row(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from app.claims.acknowledgments import apply_fetched, fetch_acknowledgment  # noqa: PLC0415
        from app.models.claims import ClaimReceipt  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415
        from tests.claims_pipeline_fakes import FakeClearinghouse  # noqa: PLC0415

        client = FakeClearinghouse()
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            created = _validated_claim(
                scoped,
                patient_id,
                coverage_ids,
                state="submitted",
                submitted_at=datetime.now(UTC) - timedelta(hours=1),
            )
            transaction = client.acknowledge("payer_accepted", created.control_number)
            fetched = fetch_acknowledgment(client, transaction)
            assert fetched is not None
            pipeline = _pipeline(scoped, _CLINICIAN_A)

            [(first, _)] = apply_fetched(pipeline, fetched, vendor_event_id="evt-pg-1")
            scoped.session.commit()
            [(second, _)] = apply_fetched(pipeline, fetched, vendor_event_id="evt-pg-1")
            scoped.session.commit()

            assert (first, second) == ("moved", "duplicate")
            saved = pipeline.claims.get(created.id)
            assert saved is not None
            assert saved.state == "payer_accepted"
            assert saved.payer_claim_number == "PYR2026090600001"
            [receipt] = pipeline.receipts.list_for_claim(created.id)
            assert receipt.vendor_event_id == "evt-pg-1"

            with pytest.raises(IntegrityError):
                pipeline.receipts.add(
                    ClaimReceipt(
                        id=str(uuid.uuid4()),
                        claim_id=created.id,
                        kind="acknowledged",
                        vendor_event_id="evt-pg-1",
                        occurred_at=datetime.now(UTC),
                    )
                )
            scoped.session.rollback()
        finally:
            scoped.close()

    def test_a_deadline_rung_fires_once_and_the_database_refuses_a_second(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from app.claims.watchdog import run_watchdog  # noqa: PLC0415
        from app.models.claims import ClaimReceipt  # noqa: PLC0415
        from app.repositories.postgres.coverage import PostgresPayerRepository  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415
        from tests.claims_fixtures import line  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            claim_id = str(uuid.uuid4())
            # Ten days left on the default 90-day filing window.
            service = datetime.now(UTC).date() - timedelta(days=80)
            created = _validated_claim(
                scoped,
                patient_id,
                coverage_ids,
                id=claim_id,
                state="draft",
                lines=[
                    line(
                        id=str(uuid.uuid4()),
                        claim_id=claim_id,
                        patient_id=patient_id,
                        service_date=service,
                        created_at=datetime.now(UTC),
                    )
                ],
            )
            pipeline = _pipeline(scoped, _CLINICIAN_A)
            payers = PostgresPayerRepository(scoped.session)
            for _ in range(2):
                run_watchdog(pipeline, payers=payers, practice_user_ids=[_CLINICIAN_A])
                scoped.session.commit()

            receipts = pipeline.receipts.list_for_claim(created.id)
            assert [(r.kind, r.deadline_kind, r.rung) for r in receipts] == [
                ("deadline_approaching", "filing", 14)
            ]
            with pytest.raises(IntegrityError):
                pipeline.receipts.add(
                    ClaimReceipt(
                        id=str(uuid.uuid4()),
                        claim_id=created.id,
                        kind="deadline_approaching",
                        deadline_kind="filing",
                        rung=14,
                        occurred_at=datetime.now(UTC),
                    )
                )
            scoped.session.rollback()
        finally:
            scoped.close()

        mine = [
            r
            for r in _reminders_visible_to(engine, tenant_schema, _CLINICIAN_A)
            if r.item_type == "claim_deadline_approaching"
            and created.control_number in (r.notes or "")
        ]
        assert len(mine) == 1
