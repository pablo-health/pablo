# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for prescribing-encounter finalization + addenda.

Against a provisioned tenant schema and a NOBYPASSRLS role (see conftest.py),
proves end to end that:

1. ``finalize_encounter`` refuses while a hard-stop ledger item is missing, and
   succeeds once it is satisfied — stamping the signature + integrity digest
   and flipping status to ``finalized``;
2. ``verify_encounter_integrity`` confirms the finalized snapshot, and detects
   an after-the-fact edit to the frozen record;
3. ``append_addendum`` chains a dated correction onto a finalized encounter,
   ``verify_addenda_chain`` confirms the chain, and an edit to a stored
   addendum breaks it; an addendum on an open encounter is refused; and
4. the addenda are isolated by the auto-applied ``has_patient_access`` RLS
   policy — a clinician without a grant sees none.

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
    """A minimal ruleset with one Schedule-II hard-stop, evidence-backed item."""
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

    schema = f"practice_test_rx_final_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_and_grant(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    patient_id = str(uuid.uuid4())
    clinician_a = "rx-final-clinician-a"
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


def _satisfy_and_finalize(session, enc, rx, clinician_a, statement="I reviewed and attest."):  # type: ignore[no-untyped-def]
    """Sync the ledger, bind evidence to the hard-stop item, finalize. Returns the ledger."""
    from app.prescribing.attestation import (  # noqa: PLC0415
        bind_evidence,
        live_checklist,
        sync_checklist,
    )
    from app.prescribing.finalization import finalize_encounter  # noqa: PLC0415

    sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
    bind_evidence(session, enc, "maps_review", "doc://maps/report/1", actor=clinician_a, now=_now())
    ledger = sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())
    finalize_encounter(
        session,
        enc,
        [rx],
        live_checklist(session, enc.id),
        signed_by=clinician_a,
        attestation_statement=statement,
        now=_now(),
    )
    return ledger


class TestFinalize:
    def test_blocked_until_hard_stop_satisfied_then_signs(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import live_checklist, sync_checklist  # noqa: PLC0415
        from app.prescribing.finalization import (  # noqa: PLC0415
            FinalizationBlockedError,
            finalize_encounter,
            verify_encounter_integrity,
        )

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            sync_checklist(session, enc, rx, _ruleset(), actor=clinician_a, now=_now())

            # maps_review is a missing hard stop -> finalization is blocked.
            with pytest.raises(FinalizationBlockedError) as exc:
                finalize_encounter(
                    session,
                    enc,
                    [rx],
                    live_checklist(session, enc_id),
                    signed_by=clinician_a,
                    attestation_statement="I attest.",
                    now=_now(),
                )
            assert exc.value.item_ids == ["maps_review"]
            assert enc.status == "open"

            # Satisfy it, then finalize succeeds and is verifiable.
            self_ledger = _satisfy_and_finalize(session, enc, rx, clinician_a)
            assert self_ledger[0].status == "satisfied"
            session.commit()

            enc, rx = _load(session, enc_id, rx_id)
            assert enc.status == "finalized"
            assert enc.finalized_by == clinician_a
            assert enc.finalized_at is not None
            assert enc.attestation_statement == "I reviewed and attest."
            assert enc.integrity_digest is not None
            assert len(enc.integrity_digest) == 64
            ledger = live_checklist(session, enc_id)
            assert verify_encounter_integrity(enc, [rx], ledger) is True
        finally:
            _close(session, s_tok, u_tok)

    def test_verify_detects_post_finalize_edit(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.attestation import live_checklist  # noqa: PLC0415
        from app.prescribing.finalization import verify_encounter_integrity  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            _satisfy_and_finalize(session, enc, rx, clinician_a)
            session.commit()

            # Tamper with the frozen prescription; the digest no longer matches.
            rx.days_supply = 90
            assert verify_encounter_integrity(enc, [rx], live_checklist(session, enc_id)) is False
        finally:
            session.rollback()
            _close(session, s_tok, u_tok)


class TestAddenda:
    def test_chain_then_tamper_detected(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.finalization import (  # noqa: PLC0415
            append_addendum,
            live_addenda,
            verify_addenda_chain,
        )

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(session, enc_id, rx_id)
            _satisfy_and_finalize(session, enc, rx, clinician_a)

            a1 = append_addendum(
                session, enc, label="typo", text="dose was 10mg", author=clinician_a, now=_now()
            )
            a2 = append_addendum(
                session,
                enc,
                label="clarify",
                text="indication ADHD",
                author=clinician_a,
                now=_now(),
            )
            session.commit()

            # First link chains off the encounter digest; second off the first.
            assert a1.prev_digest == enc.integrity_digest
            assert a2.prev_digest == a1.digest
            addenda = live_addenda(session, enc_id)
            assert [a.label for a in addenda] == ["typo", "clarify"]
            assert verify_addenda_chain(enc, addenda) is True

            # Edit a stored addendum's text -> chain verification fails.
            addenda[0].text = "dose was 80mg"
            session.flush()
            assert verify_addenda_chain(enc, live_addenda(session, enc_id)) is False
        finally:
            session.rollback()
            _close(session, s_tok, u_tok)

    def test_addendum_on_open_encounter_refused(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.finalization import (  # noqa: PLC0415
            EncounterNotFinalizedError,
            append_addendum,
        )

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        session, s_tok, u_tok = _session(engine, tenant_schema, clinician_a)
        try:
            enc, _ = _load(session, enc_id, rx_id)  # still open
            with pytest.raises(EncounterNotFinalizedError):
                append_addendum(session, enc, label="x", text="y", author=clinician_a, now=_now())
        finally:
            session.rollback()
            _close(session, s_tok, u_tok)

    def test_addenda_hidden_from_clinician_without_grant(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_and_grant: tuple[str, str],
        encounter: tuple[str, str],
    ) -> None:
        from app.prescribing.finalization import append_addendum, live_addenda  # noqa: PLC0415

        _, clinician_a = patient_and_grant
        enc_id, rx_id = encounter
        clinician_b = "rx-final-clinician-b"

        sess_a, s_a, u_a = _session(engine, tenant_schema, clinician_a)
        try:
            enc, rx = _load(sess_a, enc_id, rx_id)
            _satisfy_and_finalize(sess_a, enc, rx, clinician_a)
            append_addendum(
                sess_a, enc, label="note", text="follow-up", author=clinician_a, now=_now()
            )
            sess_a.commit()
            assert live_addenda(sess_a, enc_id), "Control: A must see the addenda"
        finally:
            _close(sess_a, s_a, u_a)

        sess_b, s_b, u_b = _session(engine, tenant_schema, clinician_b)
        try:
            assert live_addenda(sess_b, enc_id) == [], "B has no grant — addenda hidden"
        finally:
            _close(sess_b, s_b, u_b)
