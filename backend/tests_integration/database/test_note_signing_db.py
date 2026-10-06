# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sign, add to, unlock and re-sign a note against real Postgres.

The unit tests prove the rules over the in-memory repository. This proves the
Postgres repository keeps them: every signed version and every addendum still
verifies after a round trip through the database (timestamps come back in the
driver's zone, JSONB comes back re-parsed), the unlocked version keeps its
reason, and the table refuses an unlock with no reason.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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

_CLINICIAN = "5c3a9e17-2f6d-5b48-8a90-1d7e3c5f9b24"


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
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_note_signing_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _session(engine: Engine, schema: str) -> Session:
    session = Session(engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    session.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
    return session


@pytest.fixture(scope="module")
def patient(engine: Engine, tenant_schema: str) -> str:
    patient_id = str(uuid.uuid4())
    with _session(engine, tenant_schema) as session:
        session.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', "
                "'ada', 'lovelace', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        session.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _CLINICIAN},
        )
        session.commit()
    return patient_id


def test_the_whole_cycle_survives_the_database(
    engine: Engine, tenant_schema: str, patient: str
) -> None:
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.services.note_service import NoteService  # noqa: PLC0415
    from app.services.note_signing import (  # noqa: PLC0415
        NoteLockedError,
        verify_addenda_chain,
        verify_signature,
    )

    with _session(engine, tenant_schema) as session:
        service = NoteService(PostgresNotesRepository(session))
        note = service.create_standalone_note(
            patient_id=patient,
            note_type="narrative",
            content_edited={"body": "First draft."},
            user_id=_CLINICIAN,
        )
        service.sign_note(
            note.id, signer_name="Sam Ortiz", signer_credentials="LMFT", user_id=_CLINICIAN
        )
        with pytest.raises(NoteLockedError):
            service.update_note_edits(note.id, {"body": "sneaky"}, _CLINICIAN)
        for words in ("Called after the session.", "Safety plan reviewed."):
            service.add_addendum(
                note.id,
                text=words,
                signer_name="Sam Ortiz",
                signer_credentials="LMFT",
                user_id=_CLINICIAN,
            )
        service.unlock_note(note.id, reason="Wrong date in the body", user_id=_CLINICIAN)
        service.update_note_edits(note.id, {"body": "Corrected draft."}, _CLINICIAN)
        service.sign_note(
            note.id, signer_name="Sam Ortiz", signer_credentials="LMFT, LPCC", user_id=_CLINICIAN
        )
        session.commit()

    with _session(engine, tenant_schema) as session:
        service = NoteService(PostgresNotesRepository(session))
        current, versions, addenda = service.get_signing_record(note.id, _CLINICIAN)

    assert current.finalized_at is not None
    assert [v.version for v in versions] == [1, 2]
    assert all(verify_signature(v) for v in versions), "a signed version no longer verifies"
    first, second = versions
    assert first.content_edited == {"body": "First draft."}
    assert first.unlock_reason == "Wrong date in the body"
    assert first.unlocked_by == _CLINICIAN
    assert second.content_edited == {"body": "Corrected draft."}
    assert second.signer_credentials == "LMFT, LPCC"
    assert second.unlocked_at is None
    assert [a.text for a in addenda] == ["Called after the session.", "Safety plan reviewed."]
    assert verify_addenda_chain(addenda), "the addendum chain no longer verifies"


def test_an_unlock_without_a_reason_is_refused_by_the_table(
    engine: Engine, tenant_schema: str, patient: str
) -> None:
    with _session(engine, tenant_schema) as session:
        note_id = str(uuid.uuid4())
        session.execute(
            text(
                "INSERT INTO notes (id, patient_id, note_type, status, restricted, "
                "created_at, updated_at) VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), "
                "'narrative', 'complete', false, now(), now())"
            ),
            {"id": note_id, "pid": patient},
        )
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO note_signatures (id, note_id, patient_id, version, "
                    "note_type, digest, signed_by, signer_name, signed_at, unlocked_at) "
                    "VALUES (gen_random_uuid(), CAST(:id AS uuid), CAST(:pid AS uuid), 1, "
                    "'narrative', :d, CAST(:u AS uuid), 'Sam', now(), now())"
                ),
                {"id": note_id, "pid": patient, "d": "0" * 64, "u": _CLINICIAN},
            )
        session.rollback()
