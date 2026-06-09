# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for the prescribing attestation ledger (THERAPY-g79v.2.4).

Proves end-to-end, against a provisioned tenant schema and a NOBYPASSRLS role
(see conftest.py), that the ``prescribing_checklist_items`` ledger:

1. is computed by the engine — ``sync_checklist`` upserts one row per
   *applicable* item with the engine's status, and a required item with no
   evidence is ``missing`` (no checkbox without evidence);
2. reaches ``satisfied`` only after evidence is bound and the ledger re-synced;
3. soft-deletes items that stop applying (the prescription changed) rather than
   flipping them;
4. is frozen once the encounter is finalized (immutability rule); and
5. is isolated by the auto-applied ``has_patient_access`` RLS policy — a
   clinician without a grant sees nothing and a raw INSERT is rejected by the
   DB.

Each invisibility assertion is preceded by a control that the row IS visible to
the grantee first, guarding the empty-table false pass.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _ruleset():  # type: ignore[no-untyped-def]
    """A minimal prescribing ruleset exercising every status path.

    * ``maps_review`` — applies to any Schedule II; required, hard-stop,
      evidence-backed (missing until an evidence_link is bound).
    * ``acute_opioid_limit`` — conditional + computed: applies only to a
      Schedule II opioid, triggers on an acute-pain indication, satisfied when
      days_supply <= 7. Lets the Schedule II *stimulant* case prove
      applicability gating (it must NOT appear).
    """

    from app.rules.models import AppliesWhen, RuleItem, Ruleset  # noqa: PLC0415

    return Ruleset(
        id="TEST-RX",
        version="TEST-RX-2026.01",
        effective_date=date(2026, 1, 1),
        items=[
            RuleItem(
                id="maps_review",
                applies_when=AppliesWhen(schedule=("II",)),
                authority_ref="TEST-MAPS-AUTH",
                metadata={
                    "flag_behavior": "hard_stop",
                    "requirement_level": "required",
                    "evidence": True,
                },
            ),
            RuleItem(
                id="acute_opioid_limit",
                applies_when=AppliesWhen(schedule=("II",), drug_class=("opioid",)),
                metadata={
                    "flag_behavior": "soft_warn",
                    "requirement_level": "conditional",
                    "trigger": {
                        "field": "context.indication",
                        "op": "eq",
                        "value": "acute_pain",
                    },
                    "satisfied_when": {
                        "field": "prescription.days_supply",
                        "op": "lte",
                        "value": 7,
                    },
                },
            ),
        ],
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
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_rx_attest_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_and_grant(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Provision patient P with a grant for clinician A. Returns (patient_id, clinician_a)."""
    patient_id = str(uuid.uuid4())
    clinician_a = "af9b06b4-415f-5e9d-b1f9-a0ef2a88d539"
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": clinician_a},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, session_count, "
                "created_at, updated_at) VALUES (CAST(:pid AS uuid), 'Test', "
                "'Patient', 'test', 'patient', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": clinician_a},
        )
    return patient_id, clinician_a


def _session(engine: Engine, tenant_schema: str, user_id: str):  # type: ignore[no-untyped-def]
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session, schema_token, uid_token


def _close(session, schema_token, uid_token):  # type: ignore[no-untyped-def]
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


@pytest.fixture
def encounter(
    engine: Engine, tenant_schema: str, patient_and_grant: tuple[str, str]
) -> tuple[str, str]:
    """A fresh open Schedule-II stimulant encounter. Returns (encounter_id, rx_id)."""
    from app.db.models import PrescribingEncounterRow, PrescriptionRow  # noqa: PLC0415

    patient_id, clinician_a = patient_and_grant
    enc_id, rx_id = str(uuid.uuid4()), str(uuid.uuid4())
    session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
    try:
        now = _now()
        session.add(
            PrescribingEncounterRow(
                id=enc_id,
                patient_id=patient_id,
                prescriber_user_id=clinician_a,
                prescriber_type="pmhnp",
                prescriber_dea="BX1234567",
                state="MI",
                modality="audio_video",
                prior_in_person=False,
                status="open",
                created_by=clinician_a,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            PrescriptionRow(
                id=rx_id,
                encounter_id=enc_id,
                patient_id=patient_id,
                schedule="II",
                drug_class="stimulant",
                quantity=30,
                days_supply=30,
                refills=0,
                indication="adhd",
                first_in_course=True,
                created_by=clinician_a,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    finally:
        _close(session, s_tok, u_tok)
    return enc_id, rx_id


def _load(session, enc_id: str, rx_id: str):  # type: ignore[no-untyped-def]
    from app.db.models import PrescribingEncounterRow, PrescriptionRow  # noqa: PLC0415

    enc = session.get(PrescribingEncounterRow, enc_id)
    rx = session.get(PrescriptionRow, rx_id)
    return enc, rx


class TestSyncComputesLedgerFromEngine:
    def test_required_evidence_item_is_missing_and_gates(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import sync_checklist  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            rows = sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            session.commit()

            # Only the Schedule-II item applies; the opioid-gated item does not.
            assert [r.item_id for r in rows] == ["maps_review"]
            row = rows[0]
            assert row.status == "missing"
            assert row.flag_behavior == "hard_stop"
            assert row.requirement_level == "required"
            assert row.authority_ref == "TEST-MAPS-AUTH"
            assert row.ruleset_version == "TEST-RX-2026.01"
            assert row.evidence_link is None
            # The encounter is stamped with the ruleset in force.
            assert enc.ruleset_version == "TEST-RX-2026.01"
        finally:
            _close(session, s_tok, u_tok)


class TestEvidenceBindingReachesSatisfied:
    def test_bind_then_resync_flips_to_satisfied(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import (  # noqa: PLC0415
            bind_evidence,
            sync_checklist,
        )

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())

            bound = bind_evidence(
                session,
                enc,
                "maps_review",
                "doc://maps/report/123",
                actor=clinician_a,
                now=_now(),
            )
            assert bound.captured_by == clinician_a
            assert bound.captured_at is not None
            # Binding records the link; status is the engine's call on re-sync.
            assert bound.status == "missing"

            rows = sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            session.commit()
            assert rows[0].status == "satisfied"
            assert rows[0].evidence_link == "doc://maps/report/123"
        finally:
            _close(session, s_tok, u_tok)

    def test_bind_unknown_item_raises(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import bind_evidence  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, _ = _load(session, enc_id, rx_id)
            with pytest.raises(LookupError):
                bind_evidence(
                    session, enc, "not_on_ledger", "doc://x", actor=clinician_a, now=_now()
                )
        finally:
            session.rollback()
            _close(session, s_tok, u_tok)


class TestApplicabilityChangeSoftDeletes:
    def test_item_that_stops_applying_is_soft_deleted(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import live_checklist, sync_checklist  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            assert [r.item_id for r in live_checklist(session, enc_id)] == ["maps_review"]

            # Change to a Schedule IV benzo — nothing in the ruleset applies.
            rx.schedule = "IV"
            rx.drug_class = "benzodiazepine"
            rows = sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            session.commit()

            assert rows == []
            assert live_checklist(session, enc_id) == []
        finally:
            _close(session, s_tok, u_tok)


class TestFinalizedEncounterIsFrozen:
    def test_sync_and_bind_refuse_finalized_encounter(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import (  # noqa: PLC0415
            bind_evidence,
            sync_checklist,
        )
        from app.prescribing.integrity import EncounterImmutableError  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            enc.status = "finalized"
            with pytest.raises(EncounterImmutableError):
                sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            with pytest.raises(EncounterImmutableError):
                bind_evidence(session, enc, "maps_review", "doc://x", actor=clinician_a, now=_now())
        finally:
            session.rollback()
            _close(session, s_tok, u_tok)


class TestLedgerRlsIsolation:
    def test_b_cannot_see_or_insert_ledger_rows(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import live_checklist, sync_checklist  # noqa: PLC0415

        _patient_id, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        clinician_b = "acdb3b89-9b53-5748-92cd-dadcf5aeb029"

        # A computes the ledger; control assertion that A sees it.
        sess_a, s_a, u_a = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(sess_a, enc_id, rx_id)
            sync_checklist(sess_a, enc, rx, _ruleset(), actor=clinician_a, now=_now())
            sess_a.commit()
            assert live_checklist(sess_a, enc_id), "Control: A must see the ledger"
        finally:
            _close(sess_a, s_a, u_a)

        # B has no grant — the RLS policy hides every row.
        sess_b, s_b, u_b = _session(engine, tenant_schema, clinician_b)
        try:
            assert live_checklist(sess_b, enc_id) == [], "B has no grant — ledger hidden"
        finally:
            _close(sess_b, s_b, u_b)

    def test_b_raw_insert_rejected_by_rls(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        patient_id, _ = patient_and_grant
        enc_id, _rx = encounter
        clinician_b = "acdb3b89-9b53-5748-92cd-dadcf5aeb029"

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_b},
            )
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO prescribing_checklist_items "
                        "(id, encounter_id, patient_id, item_id, requirement_level, "
                        " flag_behavior, status, ruleset_version, created_by, "
                        " created_at, updated_at) VALUES "
                        "(gen_random_uuid(), CAST(:eid AS uuid), CAST(:pid AS uuid), "
                        " 'maps_review', 'required', 'hard_stop', 'missing', "
                        " 'TEST-RX-2026.01', :u, now(), now())"
                    ),
                    {"eid": enc_id, "pid": patient_id, "u": clinician_b},
                )
            conn.rollback()

        assert "row-level security" in str(exc.value).lower(), (
            f"Expected an RLS WITH CHECK violation for B's INSERT. Got: {exc.value}"
        )
