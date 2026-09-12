# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the credential record.

Five things only a real database can show, and the unit suite deliberately does
not try to:

1. A freshly-provisioned tenant carries all twelve tables — provisioning applies
   ``tenant_template.sql``, not the alembic chain, so a table can exist for every
   migrated tenant and be missing from every new one.
2. Each table is force-RLS'd with a working policy, and one clinician cannot read
   another's credential record. The conftest role is NOSUPERUSER NOBYPASSRLS, so
   this is the real boundary rather than a hopeful one.
3. The encrypted columns hold ciphertext. Asserted by reading every column of the
   row out-of-band and looking for the plaintext.
4. Decrypting writes an audit row naming the fields disclosed — and never the
   values.
5. A panel's status and its event history cannot disagree, because the writer
   emits both in one flush.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, date, datetime
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

_CLINICIAN_A = "3c5f2c0a-8b2e-5c9d-af8e-8e4a7c3e3c03"
_CLINICIAN_B = "4d6a3d1b-9c3f-5daf-b09f-9f5b8d4f4d04"

#: The tables this change introduced. Kept as a literal so a rename has to be
#: made here too, rather than quietly shrinking what provisioning is checked for.
_CREDENTIAL_TABLES = (
    "credential_government_ids",
    "credential_licenses",
    "credential_liability_policies",
    "credential_education",
    "credential_training",
    "credential_employment",
    "credential_references",
    "credential_disclosures",
    "credential_service_locations",
    "credential_bank_accounts",
    "payer_participations",
    "payer_participation_events",
)

#: Deliberately distinctive, so a substring search for them in the raw row is
#: meaningful. A realistic "123-45-6789" could collide with an id or a date.
_SSN = "071448216"
_TAX_ID = "843317905"
_DOB = date(1984, 7, 22)


@pytest.fixture(scope="module", autouse=True)
def _encryption_key() -> Iterator[None]:
    """The credential identifiers use the same key the calendar tokens do."""
    from app.settings import get_settings  # noqa: PLC0415

    previous = os.environ.get("GOOGLE_CALENDAR_ENCRYPTION_KEY")
    os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = base64.b64encode(os.urandom(32)).decode()
    get_settings.cache_clear()
    yield
    if previous is None:
        del os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"]
    else:
        os.environ["GOOGLE_CALENDAR_ENCRYPTION_KEY"] = previous
    get_settings.cache_clear()


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

    schema = f"practice_test_cred_{uuid.uuid4().hex[:8]}"
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


def _payer(session: Session, name: str, *, carveout_of: str | None = None) -> str:
    """A payer row, optionally a behavioural carve-out of another."""
    from app.db.models import PayerRow  # noqa: PLC0415

    now = datetime.now(UTC)
    row = PayerRow(
        id=str(uuid.uuid4()),
        name=name,
        payer_id=f"TEST{uuid.uuid4().hex[:6].upper()}",
        is_carveout=carveout_of is not None,
        carveout_of=carveout_of,
        enrollment_status="none",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row.id


class TestProvisioning:
    """AC 1. Provisioning applies the template, so the template must have them."""

    def test_a_fresh_tenant_carries_every_table(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            present = set(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = :s"
                    ),
                    {"s": tenant_schema},
                ).scalars()
            )
        missing = sorted(set(_CREDENTIAL_TABLES) - present)
        assert not missing, (
            f"missing from a freshly-provisioned tenant: {missing}. "
            "Re-run backend/scripts/regen_tenant_template.py and commit the result."
        )

    def test_nothing_landed_in_the_platform_schema(self, engine: Engine) -> None:
        with engine.connect() as conn:
            leaked = sorted(
                conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'platform' AND table_name = ANY(:names)"
                    ),
                    {"names": list(_CREDENTIAL_TABLES)},
                ).scalars()
            )
        assert not leaked, f"credential tables must be tenant-scoped: {leaked}"

    def test_every_table_is_force_rls_with_a_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            posture = {
                name: (rls, forced)
                for name, rls, forced in conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = ANY(:names)"
                    ),
                    {"s": tenant_schema, "names": list(_CREDENTIAL_TABLES)},
                ).all()
            }
            policied = set(
                conn.execute(
                    text("SELECT tablename FROM pg_policies WHERE schemaname = :s"),
                    {"s": tenant_schema},
                ).scalars()
            )

        for name in _CREDENTIAL_TABLES:
            assert posture.get(name) == (True, True), f"{name} is not force-RLS'd: {posture!r}"
            assert name in policied, f"{name} is force-RLS'd with NO policy — a silent deny-all"


class TestClinicianIsolation:
    """One clinician's credential record is not another's, under a real role."""

    def test_b_cannot_read_as_licence(self, engine: Engine, tenant_schema: str) -> None:
        from app.db.models import CredentialLicenseRow  # noqa: PLC0415

        now = datetime.now(UTC)
        scoped_a = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            scoped_a.session.add(
                CredentialLicenseRow(
                    id=str(uuid.uuid4()),
                    user_id=_CLINICIAN_A,
                    license_type="LCSW",
                    license_number="MI-000123",
                    state="MI",
                    expiration_date=date(2027, 5, 31),
                    status="active",
                    is_primary=True,
                    verification_source="self",
                    created_at=now,
                    updated_at=now,
                )
            )
            scoped_a.session.commit()
            mine = scoped_a.session.execute(select(CredentialLicenseRow)).scalars().all()
            assert [r.license_number for r in mine] == ["MI-000123"]
        finally:
            scoped_a.close()

        # Non-vacuous: A sees the row above, so B seeing nothing is isolation
        # rather than an empty table.
        scoped_b = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert scoped_b.session.execute(select(CredentialLicenseRow)).scalars().all() == []
        finally:
            scoped_b.close()


class TestEncryptedIdentifiers:
    """ACs 7 and 8. Ciphertext at rest, and every decryption on the record."""

    def test_plaintext_appears_in_no_column_and_a_read_is_audited(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import government_ids  # noqa: PLC0415
        from app.db.models import AuditLogRow  # noqa: PLC0415

        user = _user(_CLINICIAN_A)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            government_ids.update_identifiers(
                scoped.session,
                user,
                _audit_for(scoped.session),
                {"ssn": _SSN, "dob": _DOB, "tax_id": _TAX_ID, "tax_id_type": "ein"},
            )
            scoped.session.commit()

            summary = government_ids.load_summary(scoped.session, _CLINICIAN_A)
            assert summary["has_ssn"] is True
            assert summary["ssn_last4"] == _SSN[-4:]
            assert summary["tax_id_last4"] == _TAX_ID[-4:]

            before = scoped.session.execute(
                select(AuditLogRow).where(
                    AuditLogRow.action == "credential_identifiers_viewed",
                )
            ).all()

            got = government_ids.view_identifiers(
                scoped.session,
                user,
                _audit_for(scoped.session),
                ["ssn", "dob", "tax_id"],
            )
            scoped.session.commit()

            assert got.ssn == _SSN
            assert got.tax_id == _TAX_ID
            assert got.dob == _DOB
            # The carrier must not render its contents, so a traceback or a log
            # format string cannot leak one.
            assert _SSN not in repr(got)

            after = scoped.session.execute(
                select(AuditLogRow).where(
                    AuditLogRow.action == "credential_identifiers_viewed",
                )
            ).all()
            assert len(after) == len(before) + 1
            entry = after[-1][0]
            assert entry.changes == {"decrypted_fields": ["dob", "ssn", "tax_id"]}
            # The audit row records WHICH fields, never their values.
            for secret in (_SSN, _TAX_ID, _DOB.isoformat()):
                assert secret not in str(entry.changes)
        finally:
            scoped.close()

        # Out-of-band, past the ORM and past the service: read every column as
        # stored and look for the plaintext. The GUC has to be armed even here —
        # the table is FORCE RLS'd under a NOBYPASSRLS role, so an unarmed
        # connection sees zero rows, which would make this assertion pass
        # vacuously. Arming it reads the same row the owner reads; what is being
        # bypassed is the ORM, not the isolation boundary.
        query = (
            f"SELECT * FROM {tenant_schema}.credential_government_ids "  # noqa: S608
            "WHERE user_id = :uid"
        )
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, false)"),
                {"uid": _CLINICIAN_A},
            )
            row = conn.execute(text(query), {"uid": _CLINICIAN_A}).mappings().one()
        assert row["ssn_encrypted"], "nothing was stored, so nothing was proved"
        for column, value in row.items():
            for secret in (_SSN, _TAX_ID, _DOB.isoformat()):
                assert secret not in str(value), f"{column} holds the raw value"

    def test_clearing_a_field_removes_its_last_four_too(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Otherwise a removed SSN leaves four of its digits behind."""
        from app.credentialing import government_ids  # noqa: PLC0415

        user = _user(_CLINICIAN_B)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            audit = _audit_for(scoped.session)
            government_ids.update_identifiers(scoped.session, user, audit, {"ssn": _SSN})
            assert government_ids.load_summary(scoped.session, _CLINICIAN_B)["ssn_last4"] == "8216"

            government_ids.update_identifiers(scoped.session, user, audit, {"ssn": None})
            scoped.session.commit()
            summary = government_ids.load_summary(scoped.session, _CLINICIAN_B)
            assert summary["has_ssn"] is False
            assert summary["ssn_last4"] is None
        finally:
            scoped.close()

    def test_an_unknown_field_is_refused_rather_than_read_as_absent(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import government_ids  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            with pytest.raises(ValueError, match="social_security"):
                government_ids.view_identifiers(
                    scoped.session,
                    _user(_CLINICIAN_A),
                    _audit_for(scoped.session),
                    ["social_security"],
                )
        finally:
            scoped.close()


class TestParticipationStateMachine:
    """ACs 4, 5 and 6."""

    def test_history_reconstructs_the_current_status(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import participation  # noqa: PLC0415

        user = _user(_CLINICIAN_A)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            audit = _audit_for(scoped.session)
            payer_id = _payer(scoped.session, "Test Health Plan")

            assert participation.status_for(scoped.session, _CLINICIAN_A, payer_id) == (
                "out_of_network"
            )

            row = participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=payer_id,
                to_status="application_submitted",
            )
            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=payer_id,
                to_status="credentialed",
                credentialed_at=date(2026, 3, 2),
            )
            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=payer_id,
                to_status="contracted",
                contracted_at=date(2026, 5, 18),
            )
            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=payer_id,
                to_status="in_network",
                effective_date=date(2026, 6, 1),
            )
            scoped.session.commit()

            events = participation.history(scoped.session, row.id)
            assert [(e.from_status, e.to_status) for e in events] == [
                (None, "application_submitted"),
                ("application_submitted", "credentialed"),
                ("credentialed", "contracted"),
                ("contracted", "in_network"),
            ]
            # The whole point of the ledger: replaying it lands on the column.
            assert events[-1].to_status == row.status
            assert row.effective_date == date(2026, 6, 1)
        finally:
            scoped.close()

    def test_a_status_check_that_found_no_movement_is_still_recorded(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Which is how a stalled application becomes visible at all."""
        from app.credentialing import participation  # noqa: PLC0415

        user = _user(_CLINICIAN_A)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            audit = _audit_for(scoped.session)
            payer_id = _payer(scoped.session, "Slow Plan")
            row = participation.transition(
                scoped.session, user, audit, payer_id=payer_id, to_status="application_submitted"
            )
            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=payer_id,
                to_status="application_submitted",
                note="Called provider relations; still in queue.",
            )
            scoped.session.commit()

            events = participation.history(scoped.session, row.id)
            assert len(events) == 2
            assert events[1].from_status == "application_submitted"
            assert events[1].to_status == "application_submitted"
        finally:
            scoped.close()

    def test_credentialed_but_not_contracted_is_answerable(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import participation  # noqa: PLC0415

        user = _user(_CLINICIAN_B)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            audit = _audit_for(scoped.session)
            stuck = _payer(scoped.session, "Stuck Plan")
            fine = _payer(scoped.session, "Fine Plan")
            never = _payer(scoped.session, "Never Applied Plan")

            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=stuck,
                to_status="credentialed",
                credentialed_at=date(2024, 4, 1),
            )
            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=fine,
                to_status="in_network",
                effective_date=date(2025, 1, 1),
            )
            participation.transition(
                scoped.session, user, audit, payer_id=never, to_status="application_submitted"
            )
            scoped.session.commit()

            trapped = participation.credentialed_not_contracted(scoped.session, _CLINICIAN_B)
            assert [r.payer_id for r in trapped] == [stuck]
            assert participation.snapshot(trapped[0]).credentialed_not_contracted is True
        finally:
            scoped.close()

    def test_a_single_case_agreement_is_not_credentialed(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """An SCA is agreed without verification, so it must not read as one."""
        from app.credentialing import participation  # noqa: PLC0415

        user = _user(_CLINICIAN_A)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            payer_id = _payer(scoped.session, "SCA Plan")
            row = participation.transition(
                scoped.session,
                user,
                _audit_for(scoped.session),
                payer_id=payer_id,
                to_status="single_case_agreement",
            )
            scoped.session.commit()
            assert participation.snapshot(row).credentialed_not_contracted is False
            assert row not in participation.credentialed_not_contracted(
                scoped.session, _CLINICIAN_A
            )
        finally:
            scoped.close()

    def test_a_payer_and_its_carve_out_hold_different_statuses_at_once(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """AC 4. Two payer rows, two participations, no carve-out column."""
        from app.credentialing import participation  # noqa: PLC0415
        from app.db.models import PayerRow  # noqa: PLC0415

        user = _user(_CLINICIAN_A)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            audit = _audit_for(scoped.session)
            medical = _payer(scoped.session, "Parent Medical Plan")
            behavioral = _payer(scoped.session, "Parent Behavioral", carveout_of=medical)

            participation.transition(
                scoped.session,
                user,
                audit,
                payer_id=medical,
                to_status="in_network",
                effective_date=date(2026, 1, 1),
            )
            participation.transition(
                scoped.session, user, audit, payer_id=behavioral, to_status="out_of_network"
            )
            scoped.session.commit()

            assert participation.status_for(scoped.session, _CLINICIAN_A, medical) == "in_network"
            assert (
                participation.status_for(scoped.session, _CLINICIAN_A, behavioral)
                == "out_of_network"
            )
            # The relationship between them is already on the payer rows.
            carve = scoped.session.get(PayerRow, behavioral)
            assert carve is not None
            assert carve.is_carveout is True
            assert carve.carveout_of == medical
        finally:
            scoped.close()

    def test_an_unknown_status_is_refused_before_the_database_sees_it(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.credentialing import participation  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            payer_id = _payer(scoped.session, "Bad Status Plan")
            with pytest.raises(participation.UnknownStatusError):
                participation.transition(
                    scoped.session,
                    _user(_CLINICIAN_A),
                    _audit_for(scoped.session),
                    payer_id=payer_id,
                    to_status="paneled",
                )
        finally:
            scoped.close()


class TestDisclosureVersioning:
    """AC 10. A rewording adds a row; it never rewrites what she affirmed."""

    def test_two_versions_of_one_question_coexist(self, engine: Engine, tenant_schema: str) -> None:
        from app.credentialing import disclosures  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            disclosures.record(
                scoped.session,
                _CLINICIAN_A,
                question_key="malpractice_claim_history",
                question_version=1,
                answer=False,
            )
            disclosures.record(
                scoped.session,
                _CLINICIAN_A,
                question_key="malpractice_claim_history",
                question_version=2,
                answer=True,
                explanation="One claim in 2021, settled without a finding.",
            )
            scoped.session.commit()

            history = disclosures.history(scoped.session, _CLINICIAN_A, "malpractice_claim_history")
            assert [(r.question_version, r.answer) for r in history] == [(1, False), (2, True)]

            current = disclosures.current_answers(scoped.session, _CLINICIAN_A)
            assert current["malpractice_claim_history"].question_version == 2
        finally:
            scoped.close()

    def test_a_backfilled_old_version_does_not_displace_the_current_one(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """ "Latest" is the highest version, not the most recent write."""
        from app.credentialing import disclosures  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            disclosures.record(
                scoped.session,
                _CLINICIAN_B,
                question_key="license_action",
                question_version=3,
                answer=False,
            )
            disclosures.record(
                scoped.session,
                _CLINICIAN_B,
                question_key="license_action",
                question_version=1,
                answer=True,
                explanation="Recorded late from a 2019 application.",
            )
            scoped.session.commit()

            current = disclosures.current_answers(scoped.session, _CLINICIAN_B)
            assert current["license_action"].question_version == 3
            assert current["license_action"].answer is False
        finally:
            scoped.close()

    def test_an_unexplained_yes_is_refused(self, engine: Engine, tenant_schema: str) -> None:
        from app.credentialing import disclosures  # noqa: PLC0415

        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            with pytest.raises(disclosures.ExplanationRequiredError):
                disclosures.record(
                    scoped.session,
                    _CLINICIAN_A,
                    question_key="criminal_history",
                    question_version=1,
                    answer=True,
                )
        finally:
            scoped.close()

    def test_the_schema_refuses_it_too(self, engine: Engine, tenant_schema: str) -> None:
        """Defense in depth: the service check is not the only thing holding."""
        from app.db.models import CredentialDisclosureRow  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        now = datetime.now(UTC)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_A)
        try:
            scoped.session.add(
                CredentialDisclosureRow(
                    id=str(uuid.uuid4()),
                    user_id=_CLINICIAN_A,
                    question_key="bypassed_the_service",
                    question_version=1,
                    answer=True,
                    explanation=None,
                    answered_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            with pytest.raises(IntegrityError):
                scoped.session.flush()
            scoped.session.rollback()
        finally:
            scoped.close()


class TestClocksArePropsedNotWritten:
    """AC 11. The derivation offers a date; it never sets one."""

    def test_propose_changes_nothing(self, engine: Engine, tenant_schema: str) -> None:
        from app.credentialing import clocks  # noqa: PLC0415
        from app.db.models import ComplianceItemRow, CredentialLicenseRow  # noqa: PLC0415

        now = datetime.now(UTC)
        scoped = _TenantSession(engine, tenant_schema, _CLINICIAN_B)
        try:
            scoped.session.add(
                CredentialLicenseRow(
                    id=str(uuid.uuid4()),
                    user_id=_CLINICIAN_B,
                    license_type="LPC",
                    license_number="OH-555000",
                    state="OH",
                    expiration_date=date(2027, 9, 30),
                    status="active",
                    is_primary=True,
                    verification_source="board",
                    created_at=now,
                    updated_at=now,
                )
            )
            item = ComplianceItemRow(
                id=str(uuid.uuid4()),
                user_id=_CLINICIAN_B,
                item_type="license",
                label="Professional license",
                due_date=date(2026, 12, 31),
                created_at=now,
                updated_at=now,
            )
            scoped.session.add(item)
            scoped.session.commit()

            proposals = clocks.propose(scoped.session, _CLINICIAN_B)
            scoped.session.commit()

            assert [p.item_type for p in proposals] == ["license"]
            assert proposals[0].proposed_due_date == date(2027, 9, 30)
            assert proposals[0].current_due_date == date(2026, 12, 31)
            assert proposals[0].source_table == "credential_licenses"

            # The whole point: nothing moved until she said so.
            scoped.session.expire_all()
            unchanged = scoped.session.get(ComplianceItemRow, item.id)
            assert unchanged is not None
            assert unchanged.due_date == date(2026, 12, 31)

            clocks.apply_proposal(scoped.session, _CLINICIAN_B, proposals[0])
            scoped.session.commit()
            scoped.session.expire_all()
            applied = scoped.session.get(ComplianceItemRow, item.id)
            assert applied is not None
            assert applied.due_date == date(2027, 9, 30)

            # Agreement produces no further proposal.
            assert clocks.propose(scoped.session, _CLINICIAN_B) == []
        finally:
            scoped.close()

    def test_the_soonest_licence_expiry_wins(self, engine: Engine, tenant_schema: str) -> None:
        """One clock, several licences: the first lapse is the one that matters."""
        from app.credentialing import clocks  # noqa: PLC0415
        from app.db.models import CredentialLicenseRow  # noqa: PLC0415

        now = datetime.now(UTC)
        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            for state, number, expires in (
                ("MI", "MI-1", date(2028, 1, 31)),
                ("IL", "IL-1", date(2026, 11, 15)),
                ("IN", "IN-1", date(2027, 4, 1)),
            ):
                scoped.session.add(
                    CredentialLicenseRow(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        license_type="LCSW",
                        license_number=number,
                        state=state,
                        expiration_date=expires,
                        status="active",
                        is_primary=state == "MI",
                        verification_source="self",
                        created_at=now,
                        updated_at=now,
                    )
                )
            scoped.session.commit()

            proposals = clocks.propose(scoped.session, user_id)
            assert [p.proposed_due_date for p in proposals] == [date(2026, 11, 15)]
            # No item existed, so applying creates one.
            assert proposals[0].item_id is None
            created = clocks.apply_proposal(scoped.session, user_id, proposals[0])
            scoped.session.commit()
            assert created.item_type == "license"
            assert created.due_date == date(2026, 11, 15)
        finally:
            scoped.close()

    def test_a_revoked_licence_does_not_drive_a_renewal_reminder(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """A revoked licence has a problem a renewal nudge is the wrong answer to."""
        from app.credentialing import clocks  # noqa: PLC0415
        from app.db.models import CredentialLicenseRow  # noqa: PLC0415

        now = datetime.now(UTC)
        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            scoped.session.add(
                CredentialLicenseRow(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    license_type="LPC",
                    license_number="XX-9",
                    state="XX",
                    expiration_date=date(2026, 10, 1),
                    status="revoked",
                    is_primary=False,
                    verification_source="board",
                    created_at=now,
                    updated_at=now,
                )
            )
            scoped.session.commit()
            assert clocks.propose(scoped.session, user_id) == []
        finally:
            scoped.close()


class TestOnePrimaryLicence:
    def test_a_second_primary_is_refused(self, engine: Engine, tenant_schema: str) -> None:
        from app.db.models import CredentialLicenseRow  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        now = datetime.now(UTC)
        user_id = str(uuid.uuid4())
        scoped = _TenantSession(engine, tenant_schema, user_id)
        try:
            for state, number in (("MI", "P-1"), ("OH", "P-2")):
                scoped.session.add(
                    CredentialLicenseRow(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        license_type="LCSW",
                        license_number=number,
                        state=state,
                        status="active",
                        is_primary=True,
                        verification_source="self",
                        created_at=now,
                        updated_at=now,
                    )
                )
            with pytest.raises(IntegrityError):
                scoped.session.flush()
            scoped.session.rollback()
        finally:
            scoped.close()
