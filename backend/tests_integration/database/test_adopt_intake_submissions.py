# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading the pre-packet intake forms back as packet submissions.

The command under test moves a practice's old fixed-form submissions onto
the default intake form, so a chart has one place to read them rather than
two. Only a real database can show the two things that matter about it.

**It is idempotent, and not by remembering.** The link back to the legacy
row carries a unique index, so a second run finds the work already done
rather than doing it twice. The proof here is counts: run it, run it again,
and the row counts do not move.

**It can see the rows at all.** Every table it touches is force-RLS'd and
scoped to one patient at a time, and this connects as the same
``NOSUPERUSER NOBYPASSRLS`` role production uses — so if the command's
row-security suspension were wrong, it would read an empty practice and
cheerfully report nothing to do. A control asserts the submission is there
before the command runs, and the flags are asserted back on afterwards.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

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

_CLINICIAN = "6c2af815-3f9b-5e42-a507-81d38bf2c496"

_PHQ9 = {str(i): 1 for i in range(1, 10)}
_GAD7 = {str(i): 2 for i in range(1, 8)}


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
    """A freshly provisioned practice, which comes seeded with the form."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_adopt_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def submission(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """One patient and one legacy submission, written as the app wrote them."""
    patient_id = str(uuid.uuid4())
    submission_id = str(uuid.uuid4())
    submitted_at = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', 'ada', 'lovelace', "
                "'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _CLINICIAN},
        )

    # The submission itself is a patient write, so it goes in armed as the
    # patient — the same arm the portal route uses.
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_patient_id', :p, false)"),
            {"p": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_intake_submissions "
                "(id, patient_id, submitted_at, payload, created_by, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :at, "
                "CAST(:payload AS jsonb), :by, :at)"
            ),
            {
                "id": submission_id,
                "pid": patient_id,
                "at": submitted_at,
                "by": patient_id,
                "payload": json.dumps(
                    {
                        "form_version": 1,
                        "name_confirmed": True,
                        "dob_confirmed": False,
                        "corrections": "My date of birth is a day out.",
                        "reason_text": "Panic before every shift.",
                        "instruments": {"phq9": _PHQ9, "gad7": _GAD7},
                        "outcome_measure_ids": {},
                    }
                ),
            },
        )
    return patient_id, submission_id


_TABLES = (
    "patient_intake_assignments",
    "patient_intake_responses",
    "patient_intake_submissions",
)


def _counts(engine: Engine, schema: str) -> tuple[int, int, int]:
    """Assignments, responses and legacy submissions, read unfiltered.

    Counted as the schema owner with row security suspended for the read
    only — the same thing the command does, so the count is of what is
    there rather than of what one patient can see.
    """
    counted = []
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        for table in _TABLES:
            conn.execute(text(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY'))
        try:
            for table in _TABLES:
                counted.append(
                    int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608
                )
        finally:
            for table in _TABLES:
                conn.execute(text(f'ALTER TABLE "{schema}"."{table}" FORCE ROW LEVEL SECURITY'))
    return counted[0], counted[1], counted[2]


def _row_security(engine: Engine, schema: str, table: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = :t"
            ),
            {"s": schema, "t": table},
        ).one()
    return bool(row[0]), bool(row[1])


class TestAdoption:
    def test_the_role_really_enforces_rls(self, engine: Engine) -> None:
        """Without this, the command proving it can read proves nothing."""
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).one()
        assert not row[0], "connecting role is a superuser"
        assert not row[1], "connecting role has BYPASSRLS"

    def test_adopting_maps_the_whole_submission(
        self, engine: Engine, tenant_schema: str, submission: tuple[str, str]
    ) -> None:
        from app.bin.adopt_intake_submissions import adopt_practice  # noqa: PLC0415

        patient_id, submission_id = submission
        assert _counts(engine, tenant_schema) == (0, 0, 1), "the control: nothing adopted yet"

        result = adopt_practice(engine, tenant_schema)
        assert (result.submissions, result.adopted, result.already_adopted) == (1, 1, 0)

        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_patient_id', :p, false)"),
                {"p": patient_id},
            )
            assignment = (
                conn.execute(
                    text(
                        "SELECT id::text, status, accepted_at, submitted_at, receipt_code, "
                        "legacy_submission_id::text FROM patient_intake_assignments"
                    )
                )
                .mappings()
                .one()
            )
            answers = dict(
                conn.execute(
                    text(
                        "SELECT d.key, r.value::text FROM patient_intake_responses r "
                        "JOIN intake_item_definitions d ON d.id = r.item_id"
                    )
                ).all()
            )
            frozen = conn.execute(
                text("SELECT bool_and(NOT draft) FROM patient_intake_responses")
            ).scalar_one()

        assert assignment["status"] == "accepted"
        assert assignment["accepted_at"] == assignment["submitted_at"]
        assert assignment["legacy_submission_id"] == submission_id
        assert len(str(assignment["receipt_code"])) == 8
        assert frozen is True, "an adopted answer is a record, not a draft"

        assert set(answers) == {"demographics", "reason", "phq9", "gad7"}
        assert json.loads(answers["reason"]) == {"text": "Panic before every shift."}
        assert json.loads(answers["demographics"]) == {
            "name_confirmed": True,
            "dob_confirmed": False,
            "corrections": "My date of birth is a day out.",
        }
        assert json.loads(answers["phq9"]) == {"item_scores": _PHQ9}
        assert json.loads(answers["gad7"]) == {"item_scores": _GAD7}

    @pytest.mark.usefixtures("submission")
    def test_running_it_again_changes_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Idempotent, which is the only way "run this once" is safe."""
        from app.bin.adopt_intake_submissions import adopt_practice  # noqa: PLC0415

        before = _counts(engine, tenant_schema)
        result = adopt_practice(engine, tenant_schema)
        assert (result.submissions, result.adopted, result.already_adopted) == (1, 0, 1)
        assert _counts(engine, tenant_schema) == before

    @pytest.mark.usefixtures("submission")
    def test_the_legacy_table_is_left_alone(self, engine: Engine, tenant_schema: str) -> None:
        """Adoption copies; a later decision is what deletes."""
        _assignments, _responses, submissions = _counts(engine, tenant_schema)
        assert submissions == 1

    @pytest.mark.usefixtures("submission")
    def test_row_security_is_back_on_afterwards(self, engine: Engine, tenant_schema: str) -> None:
        """The flags the command turned off, put back exactly as they were."""
        from app.bin.adopt_intake_submissions import adopt_practice  # noqa: PLC0415

        adopt_practice(engine, tenant_schema)
        for table in _TABLES:
            assert _row_security(engine, tenant_schema, table) == (True, True), table

    @pytest.mark.usefixtures("submission")
    def test_a_dry_run_writes_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Reports what it would do, so an operator can look before running."""
        from app.bin.adopt_intake_submissions import adopt_practice  # noqa: PLC0415

        before = _counts(engine, tenant_schema)
        result = adopt_practice(engine, tenant_schema, dry_run=True)
        assert result.submissions == 1
        assert _counts(engine, tenant_schema) == before
