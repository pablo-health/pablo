# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``log_patient_principal_action`` driven through the real repository.

The seam has coverage on both sides of itself and none across it.
``tests/test_audit_service.py`` calls the method against an in-memory
repo, which cannot refuse anything; ``test_patient_audit_writes.py``
proves the policy accepts a hand-written INSERT, which the service
never issues. Between them sits the part that actually ships: the
service builds an ``AuditLogEntry``, ``PostgresAuditRepository.append``
maps it onto ``AuditLogRow``, and the row meets the policy.

That mapping is where this path is most likely to be wrong, because it
is the one place the patient id has to travel as a *clinician-shaped*
value: ``user_id`` is ``VARCHAR(128)`` holding what is elsewhere a
uuid, ``patient_id`` is a genuine ``uuid`` column, and the WITH CHECK
arm compares ``user_id::text`` against ``app.current_patient_id``. A
str/uuid mismatch anywhere along that route reads as an RLS refusal
rather than as a type error, which is a bad way to find out.

There is no endpoint test here because there is no endpoint: nothing in
this repo calls ``log_patient_principal_action`` yet, and the patient
context dependency in ``app.auth.patient_context`` is not wired into a
route. The seam ships ahead of its caller, so the repository boundary
is as far as an integration test can currently reach.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

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

_RLS_DENIED = (IntegrityError, InternalError, ProgrammingError)


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

    schema = f"practice_test_pp_service_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _session_as(engine: Engine, schema: str, guc: str, actor_id: str) -> Session:
    """A session with the tenant schema on the path and one identity armed.

    ``set_config(..., true)`` — transaction-local, as
    ``arm_current_patient_id`` does in production — so a pooled
    connection cannot carry one test's identity into the next.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    session.execute(text(f"SELECT set_config('{guc}', :a, true)"), {"a": actor_id})
    return session


def _build_request() -> MagicMock:
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = "198.51.100.7"
    request.headers = {"User-Agent": "pytest-integration/1.0"}
    return request


def _service(session: Session) -> object:
    from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
    from app.services.audit_service import AuditService  # noqa: PLC0415

    return AuditService(PostgresAuditRepository(session))


def _read_back(engine: Engine, schema: str, row_id: str) -> dict | None:
    """Read the row without the policy in the way.

    A patient-actor row is visible to no principal since the read
    narrowing, so asking through the policy would return nothing
    whether or not the write succeeded — which would make every
    assertion below vacuous.
    """
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {schema}.audit_logs NO FORCE ROW LEVEL SECURITY"))
        row = (
            conn.execute(
                text(
                    "SELECT id, user_id, actor_type, actor_component, action, "  # noqa: S608
                    "resource_type, resource_id, patient_id, session_id, "
                    "ip_address, user_agent, changes, timestamp, expires_at "
                    f"FROM {schema}.audit_logs WHERE id = CAST(:id AS uuid)"
                ),
                {"id": row_id},
            )
            .mappings()
            .one_or_none()
        )
        conn.execute(text(f"ALTER TABLE {schema}.audit_logs FORCE ROW LEVEL SECURITY"))
    return dict(row) if row is not None else None


class TestTheServiceWriteReachesPostgres:
    def test_a_patient_self_action_writes_one_row_with_both_ids(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``user_id`` carries the actor and ``patient_id`` the subject —
        the same person here, which is what makes "everything this actor
        did" and "everything about this patient" both find the row."""
        from app.models.audit import ACTOR_TYPE_PATIENT, AuditAction, ResourceType  # noqa: PLC0415

        patient_id = str(uuid.uuid4())
        session = _session_as(engine, tenant_schema, "app.current_patient_id", patient_id)
        try:
            entry = _service(session).log_patient_principal_action(  # type: ignore[attr-defined]
                action=AuditAction.PATIENT_VIEWED,
                request=_build_request(),
                patient_id=patient_id,
                resource_type=ResourceType.PATIENT,
                resource_id=patient_id,
            )
            session.commit()
        finally:
            session.close()

        row = _read_back(engine, tenant_schema, entry.id)
        assert row is not None
        assert row["user_id"] == patient_id
        assert str(row["patient_id"]) == patient_id
        assert row["actor_type"] == ACTOR_TYPE_PATIENT
        assert row["actor_component"] is None
        assert row["action"] == "patient_viewed"
        assert row["ip_address"] == "198.51.100.7"
        assert row["user_agent"] == "pytest-integration/1.0"
        delta_days = (row["expires_at"] - row["timestamp"]).days
        assert 2554 <= delta_days <= 2556

    def test_a_deployment_defined_action_code_survives_the_round_trip(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``AuditAction | str`` is a deliberate door, so a plain string
        has to land in a ``VARCHAR(50)`` column intact rather than as
        ``str(SomeEnum.X)`` or a truncation."""
        from app.models.audit import ResourceType  # noqa: PLC0415

        patient_id = str(uuid.uuid4())
        session = _session_as(engine, tenant_schema, "app.current_patient_id", patient_id)
        try:
            entry = _service(session).log_patient_principal_action(  # type: ignore[attr-defined]
                action="intake_packet_submitted",
                request=_build_request(),
                patient_id=patient_id,
                resource_type=ResourceType.PATIENT,
                resource_id=patient_id,
            )
            session.commit()
        finally:
            session.close()

        row = _read_back(engine, tenant_schema, entry.id)
        assert row is not None
        assert row["action"] == "intake_packet_submitted"

    def test_the_write_is_refused_when_the_patient_guc_is_not_armed(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The service does not arm the GUC — the request context does.

        Called outside a patient principal's request the policy must
        refuse the row rather than accept an unattributable one.
        """
        from app.models.audit import AuditAction, ResourceType  # noqa: PLC0415

        patient_id = str(uuid.uuid4())
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        session = factory()
        try:
            session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            with pytest.raises(_RLS_DENIED):
                _service(session).log_patient_principal_action(  # type: ignore[attr-defined]
                    action=AuditAction.PATIENT_VIEWED,
                    request=_build_request(),
                    patient_id=patient_id,
                    resource_type=ResourceType.PATIENT,
                    resource_id=patient_id,
                )
        finally:
            session.rollback()
            session.close()

    def test_a_patient_cannot_log_an_action_in_another_patients_name(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The service takes ``patient_id`` as an argument, so nothing in
        Python stops a caller passing someone else's. The policy has to."""
        from app.models.audit import AuditAction, ResourceType  # noqa: PLC0415

        armed = str(uuid.uuid4())
        someone_else = str(uuid.uuid4())
        session = _session_as(engine, tenant_schema, "app.current_patient_id", armed)
        try:
            with pytest.raises(_RLS_DENIED):
                _service(session).log_patient_principal_action(  # type: ignore[attr-defined]
                    action=AuditAction.PATIENT_VIEWED,
                    request=_build_request(),
                    patient_id=someone_else,
                    resource_type=ResourceType.PATIENT,
                    resource_id=someone_else,
                )
        finally:
            session.rollback()
            session.close()

    def test_the_phi_guard_refuses_before_anything_reaches_the_table(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The guard is inherited from ``_persist`` rather than restated on
        this path, which is exactly the kind of thing that is true until
        someone reorders two lines. Proven against the real table so a
        refusal that happened *after* the INSERT would still fail here."""
        from app.models.audit import AuditAction, ResourceType  # noqa: PLC0415

        patient_id = str(uuid.uuid4())
        session = _session_as(engine, tenant_schema, "app.current_patient_id", patient_id)
        try:
            with pytest.raises(ValueError, match="PHI field name"):
                _service(session).log_patient_principal_action(  # type: ignore[attr-defined]
                    action=AuditAction.PATIENT_UPDATED,
                    request=_build_request(),
                    patient_id=patient_id,
                    resource_type=ResourceType.PATIENT,
                    resource_id=patient_id,
                    changes={"first_name": {"old": "Jane", "new": "Janet"}},
                )
            session.commit()
        finally:
            session.close()

        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER TABLE {tenant_schema}.audit_logs NO FORCE ROW LEVEL SECURITY")
            )
            count = conn.execute(
                text(
                    f"SELECT count(*) FROM {tenant_schema}.audit_logs "  # noqa: S608
                    "WHERE user_id = :u AND action = 'patient_updated'"
                ),
                {"u": patient_id},
            ).scalar_one()
            conn.execute(text(f"ALTER TABLE {tenant_schema}.audit_logs FORCE ROW LEVEL SECURITY"))
        assert count == 0


class TestPatientRowsDoNotPoseAsClinicianRows:
    """``user_id`` now holds two kinds of id, and one reader assumes it
    holds one.

    ``metadata_for_review`` derives the clinician audit-review flags by
    grouping on ``user_id`` with no ``actor_type`` predicate anywhere in
    the query. A patient-actor row therefore enters that computation as
    a user who accessed a patient — and since a self-action pairs a
    patient with themselves, the pair is one no clinician baseline has
    ever seen, which is the definition ``is_novel_user_patient`` uses.

    RLS hides these rows from a clinician's own session, so the review
    surface is protected in production by something other than the query
    being right. This pins the intent rather than the accident: patient
    self-actions are not clinician access and must not be reviewed as
    such, whichever way they are reached.
    """

    def test_a_patient_self_action_is_not_flagged_as_novel_clinician_access(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415

        patient_id = str(uuid.uuid4())
        session = _session_as(engine, tenant_schema, "app.current_patient_id", patient_id)
        try:
            # A baseline old enough that the novelty check does not skip
            # this "user", plus a fresh row inside the review window.
            for days_ago in (400.0, 0.0):
                _insert_patient_row(session, patient_id, days_ago)
            session.commit()
        finally:
            session.close()

        reader = _session_as(engine, tenant_schema, "app.current_patient_id", patient_id)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {tenant_schema}.audit_logs NO FORCE ROW LEVEL SECURITY")
                )
            payload = PostgresAuditRepository(reader).metadata_for_review(window_hours=24)
        finally:
            reader.close()
            with engine.begin() as conn:
                conn.execute(
                    text(f"ALTER TABLE {tenant_schema}.audit_logs FORCE ROW LEVEL SECURITY")
                )

        mine = [row for row in payload if row["user_id"] == patient_id]
        assert mine, "the window row should be in the payload at all"
        assert not any(row["is_novel_user_patient"] for row in mine)


def _insert_patient_row(session: Session, patient_id: str, days_ago: float) -> None:
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    session.execute(
        text(
            'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, '
            "actor_type, action, resource_type, resource_id, patient_id) "
            "VALUES (CAST(:id AS uuid), :ts, :exp, :user_id, 'patient', "
            "'patient_viewed', 'patient', :resource_id, CAST(:patient_id AS uuid))"
        ),
        {
            "id": str(uuid.uuid4()),
            "ts": ts,
            "exp": ts + timedelta(days=2555),
            "user_id": patient_id,
            "resource_id": patient_id,
            "patient_id": patient_id,
        },
    )
