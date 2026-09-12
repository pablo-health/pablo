# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for contracted rates.

What only a real database shows: that a freshly-provisioned tenant carries the
table (provisioning applies ``tenant_template.sql``, not the alembic chain);
that one clinician cannot read another's rates under a NOBYPASSRLS role; that
the schema itself refuses a row whose basis and numbers disagree; and that the
variance runs end-to-end off real ``claims`` / ``claim_lines`` rows rather than
off values handed to the arithmetic.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN_A = "5e7b4e2c-ad4a-5ebf-c1af-af6c9e5f5e05"
_CLINICIAN_B = "6f8c5f3d-be5b-5fca-d2b0-b07daf6a6f06"


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

    schema = f"practice_test_rates_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class _TenantSession:
    """A tenant session armed as one clinician, the way a request opens one."""

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


def _user(user_id: str) -> Any:
    from app.models.user import User  # noqa: PLC0415

    return User(
        id=user_id,
        email=f"{user_id[:8]}@example.com",
        name="Test Clinician",
        created_at=datetime.now(UTC),
    )


def _audit_for(session: Session) -> Any:
    from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
    from app.services.audit_service import AuditService  # noqa: PLC0415

    return AuditService(PostgresAuditRepository(session))


def _payer(session: Session, name: str) -> str:
    from app.db.models import PayerRow  # noqa: PLC0415

    now = datetime.now(UTC)
    row = PayerRow(
        id=str(uuid.uuid4()),
        name=name,
        payer_id=f"TEST{uuid.uuid4().hex[:6].upper()}",
        enrollment_status="none",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row.id


def _contracted_participation(session: Session, user_id: str, payer_name: str) -> Any:
    """A participation that reached ``contracted`` — a rate cannot exist before."""
    from app.credentialing import participation  # noqa: PLC0415

    payer_id = _payer(session, payer_name)
    return participation.transition(
        session,
        _user(user_id),
        _audit_for(session),
        payer_id=payer_id,
        to_status="contracted",
        contracted_at=date(2026, 1, 15),
    )


def _adjudicated_claim(  # noqa: PLR0913 — keyword-only claim fields, not a call-site burden
    session: Session,
    *,
    user_id: str,
    payer_id: str,
    cpt: str,
    service_date: date,
    allowed_cents: int | None,
    units: int = 1,
) -> None:
    """A paid claim with one line, carrying the rendering clinician's snapshot.

    Inserted directly rather than built through assembly: this exercises the
    variance query's join and its read of ``billing_snapshot``, and going
    through claim assembly would drag in coverage, a patient chart and a
    billing profile without testing anything more about rates.
    """
    from app.db.models import (  # noqa: PLC0415
        ClaimLineRow,
        ClaimRow,
        PatientCoverageRow,
        PatientRow,
    )

    now = datetime.now(UTC)
    patient_id = str(uuid.uuid4())
    session.add(
        PatientRow(
            id=patient_id,
            first_name="Test",
            last_name="Client",
            first_name_lower="test",
            last_name_lower="client",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    # Flush before the raw grant insert: the ORM has not written the patient yet,
    # and the grant's foreign key needs it there.
    session.flush()
    # The chart's own access grant, or the claim insert fails the patient policy.
    session.execute(
        text(
            "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by) "
            "VALUES (:p, :u, 'primary', :u)"
        ),
        {"p": patient_id, "u": user_id},
    )

    # A claim's coverage is a real foreign key, so the plan has to exist.
    coverage_id = str(uuid.uuid4())
    session.add(
        PatientCoverageRow(
            id=coverage_id,
            patient_id=patient_id,
            payer_id=payer_id,
            member_id=f"M{uuid.uuid4().hex[:10].upper()}",
            subscriber_relationship="self",
            active=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()

    claim_id = str(uuid.uuid4())
    session.add(
        ClaimRow(
            id=claim_id,
            control_number=uuid.uuid4().hex[:17],
            patient_id=patient_id,
            coverage_id=coverage_id,
            payer_id=payer_id,
            state="paid",
            frequency_code="1",
            total_charge_cents=20000,
            total_paid_cents=allowed_cents or 0,
            diagnosis_codes=["F41.1"],
            billing_snapshot={"rendering_provider": {"user_id": user_id}},
            subscriber_snapshot={},
            adjudicated_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    # Flush the claim before its line: the two have no ORM relationship, so a
    # single flush does not know the line depends on it.
    session.flush()
    session.add(
        ClaimLineRow(
            id=str(uuid.uuid4()),
            claim_id=claim_id,
            patient_id=patient_id,
            line_number=1,
            line_control_number=uuid.uuid4().hex[:12],
            service_date=service_date,
            cpt=cpt,
            modifiers=[],
            units=units,
            charge_cents=20000,
            dx_pointers=[1],
            allowed_cents=allowed_cents,
            paid_cents=allowed_cents or 0,
            created_at=now,
        )
    )
    session.flush()


class TestProvisioning:
    def test_a_fresh_tenant_carries_the_table(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            present = set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = 'contracted_rates'"
                    ),
                    {"s": tenant_schema},
                ).scalars()
            )
        assert present == {"contracted_rates"}, (
            "missing from a freshly-provisioned tenant — re-run "
            "backend/scripts/regen_tenant_template.py and commit the result"
        )

    def test_it_is_force_rls_with_a_policy(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            posture = conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'contracted_rates'"
                ),
                {"s": tenant_schema},
            ).one()
            policies = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = 'contracted_rates'"
                    ),
                    {"s": tenant_schema},
                ).scalars()
            )
        assert posture == (True, True)
        assert policies, "force-RLS'd with no policy is a silent deny-all"

    def test_it_is_not_in_the_platform_schema(self, engine: Engine) -> None:
        with engine.connect() as conn:
            leaked = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'platform' AND table_name = 'contracted_rates'"
                )
            ).scalars()
        assert list(leaked) == []


class TestClinicianIsolation:
    def test_b_cannot_read_as_rates(self, engine: Engine, tenant_schema: str) -> None:
        from app.credentialing import rates  # noqa: PLC0415
        from app.db.models import ContractedRateRow  # noqa: PLC0415

        scoped_a = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            part = _contracted_participation(scoped_a.session, _CLINICIAN_A, "Isolation Plan")
            rates.record_rate(
                scoped_a.session,
                participation=part,
                cpt="90837",
                basis="fixed",
                amount_cents=14500,
                effective_date=date(2026, 1, 1),
            )
            scoped_a.session.commit()
            mine = scoped_a.session.execute(select(ContractedRateRow)).scalars().all()
            assert [r.amount_cents for r in mine] == [14500]
        finally:
            scoped_a.close()

        # Non-vacuous: A saw the row, so B seeing none is isolation.
        scoped_b = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert scoped_b.session.execute(select(ContractedRateRow)).scalars().all() == []
        finally:
            scoped_b.close()


class TestTheSchemaRefusesAnAmbiguousRow:
    """Defense in depth: the service's own check is not the only thing holding."""

    def test_a_fixed_rate_carrying_a_percent_is_refused(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.db.models import ContractedRateRow  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        now = datetime.now(UTC)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            part = _contracted_participation(scoped.session, _CLINICIAN_A, "Ambiguous Plan")
            scoped.session.add(
                ContractedRateRow(
                    id=str(uuid.uuid4()),
                    participation_id=part.id,
                    user_id=_CLINICIAN_A,
                    cpt="90834",
                    modifier="",
                    basis="fixed",
                    amount_cents=12000,
                    percent=Decimal("85"),
                    effective_date=date(2026, 1, 1),
                    created_at=now,
                    updated_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                scoped.session.flush()
            scoped.session.rollback()
        finally:
            scoped.close()

    def test_the_service_refuses_it_first_with_a_readable_message(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import rates  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            part = _contracted_participation(scoped.session, _CLINICIAN_A, "Readable Plan")
            with pytest.raises(ValueError, match="must not carry percent"):
                rates.record_rate(
                    scoped.session,
                    participation=part,
                    cpt="90834",
                    basis="fixed",
                    amount_cents=12000,
                    percent=Decimal("85"),
                    effective_date=date(2026, 1, 1),
                )
            scoped.session.rollback()
        finally:
            scoped.close()

    def test_one_rate_per_code_per_effective_date(self, engine: Engine, tenant_schema: str) -> None:
        from app.credentialing import rates  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            part = _contracted_participation(scoped.session, _CLINICIAN_A, "Duplicate Plan")
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90837",
                basis="fixed",
                amount_cents=12000,
                effective_date=date(2026, 1, 1),
            )
            # ``record_rate`` flushes, so the duplicate raises on its own call
            # rather than at a later flush.
            with pytest.raises(IntegrityError):
                rates.record_rate(
                    scoped.session,
                    participation=part,
                    cpt="90837",
                    basis="fixed",
                    amount_cents=14500,
                    effective_date=date(2026, 1, 1),
                )
            scoped.session.rollback()
        finally:
            scoped.close()


class TestVarianceEndToEnd:
    def test_underpayment_surfaces_off_real_claims(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import rates  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            part = _contracted_participation(scoped.session, user_id, "End To End Plan")
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90837",
                basis="fixed",
                amount_cents=14500,
                effective_date=date(2026, 1, 1),
            )
            _adjudicated_claim(
                scoped.session,
                user_id=user_id,
                payer_id=part.payer_id,
                cpt="90837",
                service_date=date(2026, 3, 10),
                allowed_cents=10300,
            )
            scoped.session.commit()

            report = rates.variance_report(scoped.session, user_id)
            assert list(report) == [part.payer_id]
            lines = report[part.payer_id]
            assert len(lines) == 1
            assert lines[0].status is rates.VarianceStatus.VARIANCE
            assert lines[0].variance_cents == -4200

            payer_rollup = rates.by_payer(report)
            assert payer_rollup[0].underpaid_cents == 4200
            code_rollup = rates.by_cpt(rates.all_lines(report))
            assert [g.key for g in code_rollup] == ["90837"]
            assert code_rollup[0].variance_cents == -4200
        finally:
            scoped.close()

    def test_a_claim_before_the_effective_date_reports_no_rate_on_file(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The boundary, through the query rather than the pure function."""
        from app.credentialing import rates  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            part = _contracted_participation(scoped.session, user_id, "Boundary Plan")
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90834",
                basis="fixed",
                amount_cents=9000,
                effective_date=date(2026, 6, 1),
            )
            _adjudicated_claim(
                scoped.session,
                user_id=user_id,
                payer_id=part.payer_id,
                cpt="90834",
                service_date=date(2026, 5, 31),
                allowed_cents=8000,
            )
            scoped.session.commit()

            lines = rates.all_lines(rates.variance_report(scoped.session, user_id))
            assert [line.status for line in lines] == [rates.VarianceStatus.NO_RATE_ON_FILE]
            assert lines[0].variance_cents is None
        finally:
            scoped.close()

    def test_a_percent_rate_with_no_medicare_amount_is_not_computable(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import rates  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            part = _contracted_participation(scoped.session, user_id, "Percent Plan")
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90791",
                basis="percent_of_mpfs",
                percent=Decimal("85"),
                effective_date=date(2026, 1, 1),
            )
            _adjudicated_claim(
                scoped.session,
                user_id=user_id,
                payer_id=part.payer_id,
                cpt="90791",
                service_date=date(2026, 4, 1),
                allowed_cents=15000,
            )
            scoped.session.commit()

            lines = rates.all_lines(rates.variance_report(scoped.session, user_id))
            assert [line.status for line in lines] == [rates.VarianceStatus.NOT_COMPUTABLE]

            # Entering the Medicare amount makes the same line comparable.
            stored = (
                scoped.session.execute(
                    select(rates.ContractedRateRow).where(rates.ContractedRateRow.cpt == "90791")
                )
                .scalars()
                .one()
            )
            stored.mpfs_amount_cents = 18000
            scoped.session.commit()

            lines = rates.all_lines(rates.variance_report(scoped.session, user_id))
            assert lines[0].status is rates.VarianceStatus.VARIANCE
            assert lines[0].contracted_cents == 15300
            assert lines[0].variance_cents == -300
        finally:
            scoped.close()

    def test_another_clinicians_claims_are_not_in_the_report(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The report is keyed on the claim's rendering provider, not the reader."""
        from app.credentialing import rates  # noqa: PLC0415

        mine = str(uuid.uuid4())
        theirs = str(uuid.uuid4())

        scoped = _TenantSession(engine, tenant_schema, mine)
        try:
            part = _contracted_participation(scoped.session, mine, "Shared Payer Plan")
            payer_id = part.payer_id
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90837",
                basis="fixed",
                amount_cents=14500,
                effective_date=date(2026, 1, 1),
            )
            _adjudicated_claim(
                scoped.session,
                user_id=mine,
                payer_id=payer_id,
                cpt="90837",
                service_date=date(2026, 3, 1),
                allowed_cents=10300,
            )
            scoped.session.commit()
        finally:
            scoped.close()

        # The other clinician files her own claim — same payer, same code — from
        # her own session, because a chart grant can only be written by the
        # clinician it belongs to.
        other = _TenantSession(engine, tenant_schema, theirs)
        try:
            _adjudicated_claim(
                other.session,
                user_id=theirs,
                payer_id=payer_id,
                cpt="90837",
                service_date=date(2026, 3, 2),
                allowed_cents=9000,
            )
            other.session.commit()
        finally:
            other.close()

        scoped = _TenantSession(engine, tenant_schema, mine)
        try:
            lines = rates.all_lines(rates.variance_report(scoped.session, mine))
            assert [line.allowed_cents for line in lines] == [10300]
        finally:
            scoped.close()

    def test_an_unadjudicated_claim_is_not_in_the_report(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import rates  # noqa: PLC0415
        from app.db.models import ClaimRow  # noqa: PLC0415

        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            part = _contracted_participation(scoped.session, user_id, "Unpaid Plan")
            rates.record_rate(
                scoped.session,
                participation=part,
                cpt="90847",
                basis="fixed",
                amount_cents=16000,
                effective_date=date(2026, 1, 1),
            )
            _adjudicated_claim(
                scoped.session,
                user_id=user_id,
                payer_id=part.payer_id,
                cpt="90847",
                service_date=date(2026, 3, 1),
                allowed_cents=None,
            )
            # Un-adjudicate it: a submitted claim nobody has answered yet.
            claim = scoped.session.execute(select(ClaimRow)).scalars().one()
            claim.adjudicated_at = None
            claim.state = "submitted"
            scoped.session.commit()

            assert rates.variance_report(scoped.session, user_id) == {}
        finally:
            scoped.close()
