# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the tiered intake.

Four things the unit suite cannot show, because they are properties of a
provisioned tenant rather than of the code:

1. A freshly-provisioned tenant carries ``credential_confirmations`` and the
   four columns the intake added. Provisioning applies ``tenant_template.sql``
   rather than the alembic chain, so a migrated tenant having them says
   nothing about a new one.
2. The confirmation table is force-RLS'd with a working policy, and the
   schema's own check refuses a rejection carrying no correction — the mirror
   image of the disclosure rule, enforced in the database rather than only in
   the service.
3. Tier 1 completes against the tenant-scoped record. This is the criterion an
   earlier draft of the design got wrong: the record was briefly platform-
   scoped, and a completion computed there would have been the same for every
   practice a clinician worked in.
4. One clinician's intake is not readable as another's. The conftest role is
   NOSUPERUSER NOBYPASSRLS, so this is the real boundary.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest
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

_CLINICIAN_A = "5e7b4e2c-ad4a-5ebf-c1af-af6c9e5a5e05"
_CLINICIAN_B = "6f8c5f3d-be5b-5fca-d2bf-bf7daf6b6f06"

#: The columns the intake added to the identifier row. Listed rather than
#: derived so that dropping one is a decision made here too.
_NEW_IDENTIFIER_COLUMNS = (
    "supervision_status",
    "caqh_id",
    "medicare_intent",
    "medicaid_intent",
)


@pytest.fixture(scope="module", autouse=True)
def _encryption_key() -> Iterator[None]:
    from app.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("GOOGLE_CALENDAR_ENCRYPTION_KEY")
    os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = base64.b64encode(os.urandom(32)).decode()
    get_settings.cache_clear()
    yield
    if previous is None:
        os.environ.pop("GOOGLE_CALENDAR_ENCRYPTION_KEY", None)
    else:
        os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = previous
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_intake_{uuid.uuid4().hex[:8]}"
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


class TestProvisioning:
    """A fresh tenant, not a migrated one."""

    def test_a_fresh_tenant_carries_the_confirmation_table(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            present = conn.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = 'credential_confirmations'"
                ),
                {"schema": tenant_schema},
            )
        assert present == 1

    def test_a_fresh_tenant_carries_the_new_identifier_columns(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema "
                    "AND table_name = 'credential_government_ids'"
                ),
                {"schema": tenant_schema},
            ).scalars()
            columns = set(rows)
        assert set(_NEW_IDENTIFIER_COLUMNS) <= columns

    def test_the_confirmation_table_is_force_rls_with_a_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """A forced table with no policy is a silent deny-all, not a safe default."""
        with engine.connect() as conn:
            forced = conn.scalar(
                text(
                    "SELECT c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = 'credential_confirmations'"
                ),
                {"schema": tenant_schema},
            )
            policies = conn.scalar(
                text(
                    "SELECT count(*) FROM pg_policies "
                    "WHERE schemaname = :schema AND tablename = 'credential_confirmations'"
                ),
                {"schema": tenant_schema},
            )
        assert forced is True
        assert policies and policies > 0


class TestTheSchemaEnforcesTheMirrorRule:
    def test_a_rejection_with_no_correction_is_refused_by_the_database(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``credential_disclosures`` demands an explanation for a ``true``.

        Here it is the other way round, and the check is what makes that a
        property of the record rather than of whichever service wrote it.
        """
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            now = datetime.now(UTC)
            with pytest.raises(IntegrityError):
                scoped.session.execute(
                    text(
                        "INSERT INTO credential_confirmations "
                        "(id, user_id, field_key, source, confirmed, correction, "
                        " confirmed_at, created_at, updated_at) "
                        "VALUES (:id, :uid, 'npi_number', 'nppes', false, NULL, "
                        " :now, :now, :now)"
                    ),
                    {"id": str(uuid.uuid4()), "uid": _CLINICIAN_A, "now": now},
                )
        finally:
            scoped.session.rollback()
            scoped.close()

    def test_a_confirmation_is_one_row_per_field(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Re-confirming updates. The question is "is this right now"."""
        from app.credentialing import confirmations  # noqa: PLC0415
        from app.db.models import CredentialConfirmationRow  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            confirmations.record(
                scoped.session,
                _CLINICIAN_A,
                field_key="npi_number",
                source="nppes",
                confirmed=True,
                presented_value="1999999984",
            )
            confirmations.record(
                scoped.session,
                _CLINICIAN_A,
                field_key="npi_number",
                source="nppes",
                confirmed=False,
                presented_value="1999999984",
                correction="1234567893",
            )
            scoped.session.commit()

            rows = scoped.session.execute(select(CredentialConfirmationRow)).scalars().all()
            assert len(rows) == 1
            assert rows[0].confirmed is False
            assert rows[0].correction == "1234567893"
        finally:
            scoped.close()


class TestTierOneCompletesAgainstTheTenantRecord:
    def test_a_finished_tier_one_is_claims_ready_for_her_and_nobody_else(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """AC 7, end to end and under the real role.

        Seeding is done as clinician A inside the tenant schema, and the
        completion is computed from what that schema holds. B, armed as
        herself in the same schema, sees none of it — so "Tier 1 is finished"
        is a fact about a clinician in a practice, not about the deployment.
        """
        from app.credentialing import intake, status  # noqa: PLC0415

        scoped_a = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            _seed_tier_one(scoped_a.session, _CLINICIAN_A)
            scoped_a.session.commit()

            answered = status.answered_keys(scoped_a.session, _CLINICIAN_A)
            assert intake.claims_ready(answered)

            by_tier = {c.tier: c for c in intake.completion(answered)}
            assert by_tier[intake.Tier.CLAIMS_READY].complete
            assert by_tier[intake.Tier.CREDENTIALING].answered == 0
        finally:
            scoped_a.close()

        scoped_b = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            answered_b = status.answered_keys(scoped_b.session, _CLINICIAN_B)
            assert not intake.claims_ready(answered_b)
            assert answered_b == set()
        finally:
            scoped_b.close()

    def test_one_clinicians_confirmations_are_not_the_others(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import confirmations  # noqa: PLC0415

        scoped_a = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            confirmations.record(
                scoped_a.session,
                _CLINICIAN_A,
                field_key="exclusion_clearance",
                source="leie_sam",
                confirmed=True,
            )
            scoped_a.session.commit()
            # Non-vacuous: A sees her own row, so B seeing none is isolation
            # rather than an empty table.
            assert len(confirmations.list_for(scoped_a.session, _CLINICIAN_A)) == 1
        finally:
            scoped_a.close()

        scoped_b = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert confirmations.list_for(scoped_b.session, _CLINICIAN_B) == []
        finally:
            scoped_b.close()


def _seed_tier_one(session: Session, user_id: str) -> None:
    """Every required Tier-1 answer, written the way the routes write them."""
    from app.credentialing import government_ids  # noqa: PLC0415
    from app.db.models import (  # noqa: PLC0415
        ComplianceDocumentRow,
        CredentialBankAccountRow,
        CredentialLiabilityPolicyRow,
        CredentialLicenseRow,
        CredentialServiceLocationRow,
        PayerParticipationRow,
        PayerRow,
    )

    now = datetime.now(UTC)
    government_ids.update_identifiers(
        session,
        _user(user_id),
        _audit_for(session),
        {
            "dob": date(1985, 4, 2),
            "supervision_status": "independent",
            "business_structure": "sole_proprietor",
        },
    )

    document = ComplianceDocumentRow(
        id=str(uuid.uuid4()),
        filename="coi.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        storage_uri="gs://vault/coi.pdf",
        document_type="liability_certificate",
        uploaded_at=now,
        uploaded_by_user_id=user_id,
    )
    session.add(document)
    session.add(
        CredentialLicenseRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            license_type="LPC",
            license_number=f"GA-{uuid.uuid4().hex[:6]}",
            state="GA",
            expiration_date=date(2028, 1, 31),
            status="active",
            is_primary=True,
            verification_source="self",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialLiabilityPolicyRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            carrier_name="CPH & Associates",
            policy_number="P-100200",
            document_id=document.id,
            is_current=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialServiceLocationRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            address_line1="1 Peachtree St",
            city="Atlanta",
            state="GA",
            postal_code="30303",
            is_primary=True,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        CredentialBankAccountRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            account_holder_name="Test Clinician",
            routing_number_encrypted="ciphertext",
            account_number_encrypted="ciphertext",
            account_type="checking",
            document_id=document.id,
            created_at=now,
            updated_at=now,
        )
    )
    payer = PayerRow(
        id=str(uuid.uuid4()),
        name="Aetna",
        payer_id=f"TEST{uuid.uuid4().hex[:6].upper()}",
        enrollment_status="none",
        created_at=now,
        updated_at=now,
    )
    session.add(payer)
    session.flush()
    session.add(
        PayerParticipationRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            payer_id=payer.id,
            status="in_network",
            effective_date=date(2026, 1, 1),
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
