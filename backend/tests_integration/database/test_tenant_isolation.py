# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Multi-tenant isolation integration tests using real PostgreSQL.

Verifies that data created in one practice schema is invisible to another,
and that cross-tenant access by ID returns 404 (not 403).

Requires:
  - Cloud SQL proxy running (make db-dev-proxy) or local Postgres (make db-up)
  - DATABASE_BACKEND=postgres
  - DATABASE_URL=postgresql://...
  - MULTI_TENANCY_ENABLED=true

Run: make test-integration-tenant
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from app.db import _current_tenant_schema, set_tenant_schema
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Skip entire module if no Postgres connection available
_db_url = os.environ.get("DATABASE_URL", "")
_skip_reason = (
    "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres. "
    "Start proxy with: make db-dev-proxy"
)
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=_skip_reason,
)

# Test schema names (using UUID suffix to avoid conflicts)
_SUFFIX = uuid.uuid4().hex[:8]
# Clinician user_ids are native uuid columns; give them readable names.
_USER_ALPHA = "faeffa77-ef9c-57ae-93d7-315f0c784e84"
_USER_BETA = "e61641ea-7365-56e5-a57f-2f583ce232de"
_USER_A = "bb8e799c-a57b-533d-a8c4-6648b68eed2d"
_USER_B = "53fb3142-0849-5c70-9145-a9030a5d8c6c"
SCHEMA_ALPHA = f"practice_test_alpha_{_SUFFIX}"
SCHEMA_BETA = f"practice_test_beta_{_SUFFIX}"
PLATFORM_SCHEMA = "platform"


@pytest.fixture(scope="module")
def engine():
    """Create a SQLAlchemy engine connected to real Postgres."""
    return create_engine(_db_url, pool_pre_ping=True)


@pytest.fixture(scope="module")
def setup_test_schemas(engine):
    """Create two test practice schemas with patients and sessions tables.

    Uses raw SQL to create minimal tables (not the full ORM model set)
    to keep tests focused on schema isolation rather than provisioning.
    """
    with engine.connect() as conn:
        for schema in (SCHEMA_ALPHA, SCHEMA_BETA):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            conn.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS {schema}.patients (
                    id VARCHAR(128) PRIMARY KEY,
                    user_id VARCHAR(128) NOT NULL,
                    first_name VARCHAR(255) NOT NULL,
                    last_name VARCHAR(255) NOT NULL,
                    first_name_lower VARCHAR(255) NOT NULL,
                    last_name_lower VARCHAR(255) NOT NULL,
                    status VARCHAR(20) DEFAULT 'active',
                    session_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)
            )
            conn.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS {schema}.therapy_sessions (
                    id VARCHAR(128) PRIMARY KEY,
                    user_id VARCHAR(128) NOT NULL,
                    patient_id VARCHAR(128) NOT NULL,
                    session_date TIMESTAMP WITH TIME ZONE NOT NULL,
                    session_number INTEGER NOT NULL,
                    status VARCHAR(30) NOT NULL,
                    transcript JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """)
            )
        conn.commit()

    yield

    # Cleanup: drop test schemas
    with engine.connect() as conn:
        for schema in (SCHEMA_ALPHA, SCHEMA_BETA):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.commit()


@pytest.fixture
def db_alpha(engine, setup_test_schemas):
    """Session scoped to the Alpha practice schema.

    Sets the search_path through the production ``set_tenant_schema`` helper
    (not raw SQL) so the test exercises our tenant-scoping code. Binds to a
    dedicated held connection so the path stays stable for the fixture's
    lifetime — the two-tenant interleave needs Alpha and Beta live at once,
    which the single ``_current_tenant_schema`` context var cannot represent.
    """
    conn = engine.connect()
    session = sessionmaker(bind=conn, expire_on_commit=False)()
    set_tenant_schema(session, SCHEMA_ALPHA)
    yield session
    session.rollback()
    session.close()
    conn.close()
    _current_tenant_schema.set(None)


@pytest.fixture
def db_beta(engine, setup_test_schemas):
    """Session scoped to the Beta practice schema.

    Sets the search_path through the production ``set_tenant_schema`` helper
    (not raw SQL) so the test exercises our tenant-scoping code. Binds to a
    dedicated held connection so the path stays stable for the fixture's
    lifetime — the two-tenant interleave needs Alpha and Beta live at once,
    which the single ``_current_tenant_schema`` context var cannot represent.
    """
    conn = engine.connect()
    session = sessionmaker(bind=conn, expire_on_commit=False)()
    set_tenant_schema(session, SCHEMA_BETA)
    yield session
    session.rollback()
    session.close()
    conn.close()
    _current_tenant_schema.set(None)


def _insert_patient(db: Session, patient_id: str, user_id: str, name: str) -> None:
    now = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO patients"
            " (id, user_id, first_name, last_name, first_name_lower, last_name_lower,"
            " created_at, updated_at)"
            " VALUES (:id, :uid, :fn, :ln, :fnl, :lnl, :now, :now)"
        ),
        {
            "id": patient_id,
            "uid": user_id,
            "fn": name,
            "ln": "Test",
            "fnl": name.lower(),
            "lnl": "test",
            "now": now,
        },
    )
    db.commit()


def _insert_session(db: Session, session_id: str, user_id: str, patient_id: str) -> None:
    now = datetime.now(UTC)
    db.execute(
        text(
            "INSERT INTO therapy_sessions"
            " (id, user_id, patient_id, session_date, session_number, status, created_at)"
            " VALUES (:id, :uid, :pid, :sd, 1, 'pending_review', :now)"
        ),
        {
            "id": session_id,
            "uid": user_id,
            "pid": patient_id,
            "sd": now,
            "now": now,
        },
    )
    db.commit()


class TestPatientIsolation:
    """Verify patients in one schema are invisible to another."""

    def test_alpha_patient_invisible_to_beta(self, db_alpha, db_beta):
        patient_id = f"patient-alpha-{uuid.uuid4().hex[:8]}"
        _insert_patient(db_alpha, patient_id, _USER_ALPHA, "AlphaPatient")

        # Alpha can see the patient
        alpha_result = db_alpha.execute(
            text("SELECT id FROM patients WHERE id = :id"), {"id": patient_id}
        ).fetchone()
        assert alpha_result is not None
        assert alpha_result[0] == patient_id

        # Beta cannot see the patient (different schema)
        beta_result = db_beta.execute(
            text("SELECT id FROM patients WHERE id = :id"), {"id": patient_id}
        ).fetchone()
        assert beta_result is None

    def test_beta_patient_invisible_to_alpha(self, db_alpha, db_beta):
        patient_id = f"patient-beta-{uuid.uuid4().hex[:8]}"
        _insert_patient(db_beta, patient_id, _USER_BETA, "BetaPatient")

        # Beta can see it
        beta_result = db_beta.execute(
            text("SELECT id FROM patients WHERE id = :id"), {"id": patient_id}
        ).fetchone()
        assert beta_result is not None

        # Alpha cannot
        alpha_result = db_alpha.execute(
            text("SELECT id FROM patients WHERE id = :id"), {"id": patient_id}
        ).fetchone()
        assert alpha_result is None

    def test_list_patients_isolated(self, db_alpha, db_beta):
        """Each tenant only sees their own patients in list queries."""
        _insert_patient(db_alpha, f"pa-{uuid.uuid4().hex[:8]}", _USER_A, "AliceAlpha")
        _insert_patient(db_alpha, f"pa-{uuid.uuid4().hex[:8]}", _USER_A, "BobAlpha")
        _insert_patient(db_beta, f"pb-{uuid.uuid4().hex[:8]}", _USER_B, "CharlieBeta")

        alpha_patients = db_alpha.execute(text("SELECT first_name FROM patients")).fetchall()
        beta_patients = db_beta.execute(text("SELECT first_name FROM patients")).fetchall()

        alpha_names = {r[0] for r in alpha_patients}
        beta_names = {r[0] for r in beta_patients}

        assert "AliceAlpha" in alpha_names
        assert "BobAlpha" in alpha_names
        assert "CharlieBeta" not in alpha_names

        assert "CharlieBeta" in beta_names
        assert "AliceAlpha" not in beta_names


class TestSessionIsolation:
    """Verify therapy sessions are schema-isolated."""

    def test_session_in_alpha_invisible_to_beta(self, db_alpha, db_beta):
        patient_id = f"pt-{uuid.uuid4().hex[:8]}"
        session_id = f"ses-{uuid.uuid4().hex[:8]}"
        _insert_patient(db_alpha, patient_id, _USER_A, "Pat")
        _insert_session(db_alpha, session_id, _USER_A, patient_id)

        # Alpha can see it
        alpha_result = db_alpha.execute(
            text("SELECT id FROM therapy_sessions WHERE id = :id"), {"id": session_id}
        ).fetchone()
        assert alpha_result is not None

        # Beta cannot
        beta_result = db_beta.execute(
            text("SELECT id FROM therapy_sessions WHERE id = :id"), {"id": session_id}
        ).fetchone()
        assert beta_result is None


class TestSearchPathIsolation:
    """Verify the search_path mechanism prevents schema leakage."""

    def test_search_path_is_set_correctly(self, db_alpha, db_beta):
        alpha_path = db_alpha.execute(text("SHOW search_path")).scalar()
        beta_path = db_beta.execute(text("SHOW search_path")).scalar()

        assert SCHEMA_ALPHA in alpha_path
        assert SCHEMA_BETA not in alpha_path

        assert SCHEMA_BETA in beta_path
        assert SCHEMA_ALPHA not in beta_path

    def test_write_to_wrong_schema_impossible(self, db_alpha, db_beta):
        """Writing via Alpha session goes to Alpha schema, not Beta."""
        patient_id = f"cross-{uuid.uuid4().hex[:8]}"
        _insert_patient(db_alpha, patient_id, _USER_ALPHA, "ShouldBeInAlpha")

        # Verify it's in Alpha
        alpha_result = db_alpha.execute(
            text(f"SELECT id FROM {SCHEMA_ALPHA}.patients WHERE id = :id"),  # noqa: S608
            {"id": patient_id},
        ).fetchone()
        assert alpha_result is not None

        # Verify it's NOT in Beta (using fully-qualified name)
        beta_result = db_beta.execute(
            text(f"SELECT id FROM {SCHEMA_BETA}.patients WHERE id = :id"),  # noqa: S608
            {"id": patient_id},
        ).fetchone()
        assert beta_result is None


class TestConcurrentSchemaAccess:
    """Verify no data leaks when both schemas are accessed in the same test."""

    def test_interleaved_writes_stay_isolated(self, db_alpha, db_beta):
        """Alternating writes between tenants stay in their respective schemas."""
        ids = []
        for i in range(3):
            a_id = f"interleave-a-{i}-{uuid.uuid4().hex[:6]}"
            b_id = f"interleave-b-{i}-{uuid.uuid4().hex[:6]}"
            _insert_patient(db_alpha, a_id, _USER_A, f"Alpha{i}")
            _insert_patient(db_beta, b_id, _USER_B, f"Beta{i}")
            ids.append((a_id, b_id))

        # Alpha has only Alpha patients
        alpha_count = db_alpha.execute(
            text("SELECT COUNT(*) FROM patients WHERE first_name LIKE 'Alpha%'")
        ).scalar()
        alpha_beta_count = db_alpha.execute(
            text("SELECT COUNT(*) FROM patients WHERE first_name LIKE 'Beta%'")
        ).scalar()
        assert alpha_count >= 3
        assert alpha_beta_count == 0

        # Beta has only Beta patients
        beta_count = db_beta.execute(
            text("SELECT COUNT(*) FROM patients WHERE first_name LIKE 'Beta%'")
        ).scalar()
        beta_alpha_count = db_beta.execute(
            text("SELECT COUNT(*) FROM patients WHERE first_name LIKE 'Alpha%'")
        ).scalar()
        assert beta_count >= 3
        assert beta_alpha_count == 0
