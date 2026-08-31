# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An empty principal id must not be a bucket every principal shares.

Disarming a principal sets its GUC to ``''`` rather than dropping it —
``_disarm_other_principal`` (``app/db/__init__.py:549``) and the
``after_begin`` listener both write ``user_id or ""``. So
``app.current_user_id`` is ``''`` on every patient-request transaction,
and ``app.current_patient_id`` is ``''`` on every clinician one. That is
the normal state of every request, not an edge case.

Two docstrings justify the choice on the grounds that "the ``::text``-cast
idiom every policy uses treats ``''`` as matching no row". That holds for
every other policy in the schema, because every other ``user_id`` and
``patient_id`` column is a ``uuid`` and ``''`` fails the cast. It does not
hold here: ``audit_logs.user_id`` is the schema's only ``character
varying`` principal column, so ``''`` is a storable, matchable value and
``'' = ''`` is true.

That turns the empty id into a shared commons on the one table whose whole
job is saying who did what:

  * a patient principal READS every ``user_id = ''`` row, though the policy
    comment states a patient principal reads nothing at all;
  * a patient principal WRITES rows attributed to ``actor_type='clinician'``,
    because the clinician arm is satisfied by ``'' = ''``;
  * nobody legitimate can ever see them afterwards — no real clinician's GUC
    is ``''`` — and the retention purge runs with the GUC unset (NULL, not
    ``''``), so they never expire either.

The fix is a column constraint rather than a policy arm: it holds however
the GUCs are cleared, it cannot be forgotten by a future policy edit, and
``audit_logs.user_id`` is the only column in the schema that needs it.

These drive the real arming primitives — no hand-written ``set_config`` —
so what they exercise is the state an ordinary request produces.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import _current_patient_id, _current_user_id
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

_REFUSED = (IntegrityError, InternalError, ProgrammingError)


@pytest.fixture(autouse=True)
def _clean_principal_contextvars() -> Iterator[None]:
    """Start every test with no principal armed, and restore on the way out.

    ``arm_current_user_id`` / ``arm_current_patient_id`` stash the id in a
    ContextVar as well as the GUC, and a ContextVar outlives the test that
    set it. Leaving one set means the next test to arm the *other*
    principal presents both carriers to the ``after_begin`` listener, which
    refuses the transaction — so this file would pass alone and error the
    whole rest of the suite in a full run. Test pollution rather than a
    product bug: ``DatabaseSessionMiddleware`` clears both at request end.

    Same fixture as ``test_patient_guc_integration``, for the same reason.
    """
    patient_token = _current_patient_id.set(None)
    user_token = _current_user_id.set(None)
    yield
    _current_patient_id.reset(patient_token)
    _current_user_id.reset(user_token)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_bucket_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Session:
    from app.db import arm_current_patient_id  # noqa: PLC0415

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    arm_current_patient_id(session, patient_id)
    return session


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Session:
    from app.db import arm_current_user_id  # noqa: PLC0415

    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session


def _insert(session: Session, *, user_id: str, actor_type: str, tag: str) -> None:
    session.execute(
        text(
            'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, '
            "actor_type, action, resource_type, resource_id) "
            "VALUES (CAST(:id AS uuid), now(), now() + interval '6 years', "
            ":user_id, :actor_type, 'read', 'patient', :tag)"
        ),
        {"id": str(uuid.uuid4()), "user_id": user_id, "actor_type": actor_type, "tag": tag},
    )


class TestTheDisarmedGucIsEmptyNotAbsent:
    """Pins the mechanism the rest of the file is about.

    Green today and expected to stay green — this is not a defect, it is
    the documented behaviour of the disarm. It is asserted so that the
    tests below cannot be misread as being about some exotic state.
    """

    def test_arming_a_patient_leaves_the_clinician_guc_empty(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        session = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            armed = session.execute(
                text("SELECT current_setting('app.current_user_id', true)")
            ).scalar_one()
        finally:
            session.rollback()
            session.close()

        assert armed == ""

    def test_arming_a_clinician_leaves_the_patient_guc_empty(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        session = _as_clinician(engine, tenant_schema, str(uuid.uuid4()))
        try:
            armed = session.execute(
                text("SELECT current_setting('app.current_patient_id', true)")
            ).scalar_one()
        finally:
            session.rollback()
            session.close()

        assert armed == ""


class TestNoPrincipalCanWriteAnEmptyAttributedRow:
    """The row must not be creatable in the first place.

    Refusing the write is what closes the read and the immortality at
    once: a row that cannot exist cannot be shared, and cannot outlive a
    retention window it is invisible to.
    """

    def test_a_patient_principal_cannot_write_a_clinician_attributed_row(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The clinician arm is ``actor_type IS DISTINCT FROM 'patient' AND
        user_id::text = current_setting('app.current_user_id', true)``, and
        on a patient transaction that GUC is ``''``. An authenticated
        patient writing rows attributed to a clinician is the sharpest
        form of this: § 164.312(b) says who accessed what, and this lets
        one principal answer for another."""
        session = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            with pytest.raises(_REFUSED):
                _insert(session, user_id="", actor_type="clinician", tag="FORGED_CLIN")
        finally:
            session.rollback()
            session.close()

    def test_a_patient_principal_cannot_write_an_empty_patient_row(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The patient arm is reachable the same way from the other side.

        ``arm_current_patient_id`` refuses an empty id already; the column
        has to refuse it too, because the service seam that builds the row
        (``log_patient_principal_action``) takes ``patient_id`` as an
        argument and does not check it.
        """
        session = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            with pytest.raises(_REFUSED):
                _insert(session, user_id="", actor_type="patient", tag="FORGED_PAT")
        finally:
            session.rollback()
            session.close()

    def test_a_clinician_principal_cannot_write_an_empty_attributed_row(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Symmetry: a clinician session has ``app.current_patient_id = ''``
        for the same reason, so the patient arm is satisfiable from there."""
        session = _as_clinician(engine, tenant_schema, str(uuid.uuid4()))
        try:
            with pytest.raises(_REFUSED):
                _insert(session, user_id="", actor_type="patient", tag="FORGED_FROM_CLIN")
        finally:
            session.rollback()
            session.close()


class TestThePolicyComentAboutPatientReadsHolds:
    """ "A patient principal reads nothing at all" — as written, and in fact."""

    def test_a_patient_principal_reads_nothing_a_clinician_wrote(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """An honest clinician row must stay invisible, which it already is.

        The bug this guards is the empty-id row sitting alongside it: with
        the write refused there is nothing in the shared bucket to read,
        and the comment becomes true rather than nearly true.
        """
        clinician = str(uuid.uuid4())
        writer = _as_clinician(engine, tenant_schema, clinician)
        try:
            _insert(writer, user_id=clinician, actor_type="clinician", tag="HONEST")
            writer.commit()
        finally:
            writer.close()

        reader = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            visible = reader.execute(text("SELECT resource_id FROM audit_logs")).fetchall()
        finally:
            reader.rollback()
            reader.close()

        assert visible == []

    def test_two_patients_cannot_reach_each_other_through_the_empty_bucket(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The bucket is shared by every principal whose other GUC is
        cleared, so it is a channel between patients as much as across
        the clinician boundary."""
        first = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            with pytest.raises(_REFUSED):
                _insert(first, user_id="", actor_type="patient", tag="DROP_BOX")
        finally:
            first.rollback()
            first.close()

        second = _as_patient(engine, tenant_schema, str(uuid.uuid4()))
        try:
            visible = second.execute(text("SELECT resource_id FROM audit_logs")).fetchall()
        finally:
            second.rollback()
            second.close()

        assert visible == []
