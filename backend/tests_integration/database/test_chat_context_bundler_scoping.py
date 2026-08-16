# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres patient-boundary proof for the chat context bundler (THERAPY-r3c).

``assemble_context_bundle`` (``app.services.chat_context_bundler``) is a pure
function over whatever a ``NotesRepository`` / ``PatientDocumentRepository``
hand it — it does no scoping of its own. All patient isolation lives one
layer down, in the Postgres repos' ``patient_id`` filters plus the
``has_patient_access`` RLS policies (see ``backend/app/db/__init__.py``,
``enable_rls_on_schema``). The unit suite (``backend/tests/test_chat_context_
bundler.py``) exercises the bundler's own logic against in-memory fakes and
therefore can't prove that boundary; this module proves it against a real
provisioned tenant schema with a real Postgres role that has NOSUPERUSER
NOBYPASSRLS (see conftest.py), mirroring ``test_outcome_measures_rls.py``.

Two of the four checks below are the load-bearing ones:

* ``TestSqlFilterPresence`` regex-asserts the *compiled SQL* the repos emit
  contains an explicit ``patient_id = <bound param>`` predicate bound to the
  correct value. A behavioral assertion ("patient B's marker isn't in the
  bundle") can pass by coincidence if the seeded data is too thin — e.g. if
  the app-layer filter were silently dropped, RLS alone would NOT catch it,
  because ``has_patient_access(patient_id, user)`` is satisfied by *any*
  patient the caller has a grant on, not specifically the one they asked
  for. Every isolation test below therefore grants the same clinician
  access to *both* patients, so a dropped filter has real cross-patient
  data available to leak into — thin/absent data would let the behavioral
  checks pass vacuously either way.
* ``TestPatientIsolation`` is the behavioral counterpart: it proves the
  bundle assembled for patient A never contains patient B's content even
  though the clinician can read both charts.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import Note, PatientDocument
    from app.repositories.postgres.note import PostgresNotesRepository
    from app.repositories.postgres.patient_document import (
        PostgresPatientDocumentRepository,
    )
    from app.services.chat_context_bundler import ContextBundle
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


# ---------------------------------------------------------------------------
# Module-scoped fixtures: alembic head + provisioned tenant schema
# ---------------------------------------------------------------------------


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

    # Warm the pool so the RLS policy CREATEs that reference
    # ``has_patient_access`` (which lives in the ``practice`` template
    # schema) can resolve the function name.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_ccb_scope_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


# ---------------------------------------------------------------------------
# Seeding + repo-wiring helpers
# ---------------------------------------------------------------------------


def _seed_patient(engine: Engine, tenant_schema: str, *, grantees: list[str]) -> str:
    """Create a patient row with a ``patient_clinicians`` grant for each of
    ``grantees``. Returns the new patient id.

    Each grant row is inserted with the GUC armed to *that grantee* — the
    ``patient_clinicians`` RLS policy requires ``user_id = current GUC`` on
    INSERT (see ``enable_rls_on_schema``'s note on the grant table), so a
    single admin identity can't self-service-grant on someone else's
    behalf; each clinician "claims" their own row.
    """
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": grantees[0]},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', "
                "'test', 'patient', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        for grantee in grantees:
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": grantee},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": grantee},
            )
    return patient_id


def _make_repos(
    engine: Engine, tenant_schema: str, user_id: str
) -> tuple[PostgresNotesRepository, PostgresPatientDocumentRepository, Session, object, object]:
    """Wire a ``(notes_repo, patient_documents_repo)`` pair to a real tenant session."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.repositories.postgres.patient_document import (  # noqa: PLC0415
        PostgresPatientDocumentRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    notes_repo = PostgresNotesRepository(session)
    docs_repo = PostgresPatientDocumentRepository(session)
    return notes_repo, docs_repo, session, schema_token, uid_token


def _cleanup_tokens(session: Session, schema_token: object, uid_token: object) -> None:
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)  # type: ignore[arg-type]
    _current_user_id.reset(uid_token)  # type: ignore[arg-type]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _make_note(patient_id: str, *, marker: str) -> Note:
    from app.models import Note  # noqa: PLC0415

    ts = _now()
    return Note(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        note_type="narrative",
        created_at=ts,
        updated_at=ts,
        finalized_at=ts,
        content={"text": marker},
    )


def _make_document(patient_id: str, user_id: str, *, marker: str) -> PatientDocument:
    from app.models import DocumentCategory, PatientDocument  # noqa: PLC0415

    ts = _now()
    return PatientDocument(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        user_id=user_id,
        filename=f"{marker}.txt",
        mime_type="text/plain",
        gcs_path=f"gs://test-bucket/{marker}",
        size_bytes=len(marker),
        created_at=ts,
        finalized_at=ts,
        extracted_text=marker,
        category=DocumentCategory.CHART,
    )


def _assemble(
    notes_repo: PostgresNotesRepository,
    docs_repo: PostgresPatientDocumentRepository,
    *,
    patient_id: str,
    user_id: str,
) -> ContextBundle:
    from app.services.chat_context_bundler import assemble_context_bundle  # noqa: PLC0415

    return assemble_context_bundle(
        notes_repo=notes_repo,
        patient_documents_repo=docs_repo,
        patient_id=patient_id,
        user_id=user_id,
        selection={
            "progress_notes_recent": {"limit": 50},
            "patient_documents": True,
            "document_manifest": True,
        },
    )


# ---------------------------------------------------------------------------
# Patient isolation — the clinician can read BOTH patients (see module
# docstring: this is what makes the check non-vacuous under RLS).
# ---------------------------------------------------------------------------


class TestPatientIsolation:
    """Bundle for patient A must never surface patient B's records, even
    though the requesting clinician has a live grant on both."""

    def test_bundle_excludes_other_patient_notes_and_documents(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        clinician = str(uuid.uuid4())
        patient_a = _seed_patient(engine, tenant_schema, grantees=[clinician])
        patient_b = _seed_patient(engine, tenant_schema, grantees=[clinician])

        notes_repo, docs_repo, session, s_tok, u_tok = _make_repos(engine, tenant_schema, clinician)
        try:
            notes_repo.add(_make_note(patient_a, marker="MARKER-NOTE-A"), clinician)
            notes_repo.add(_make_note(patient_b, marker="MARKER-NOTE-B"), clinician)
            docs_repo.add(_make_document(patient_a, clinician, marker="MARKER-DOC-A"))
            docs_repo.add(_make_document(patient_b, clinician, marker="MARKER-DOC-B"))
            session.commit()

            bundle_a = _assemble(notes_repo, docs_repo, patient_id=patient_a, user_id=clinician)
            bundle_b = _assemble(notes_repo, docs_repo, patient_id=patient_b, user_id=clinician)
            expected_doc_ids_a = [d.id for d in docs_repo.list_for_patient(patient_a, clinician)]
        finally:
            _cleanup_tokens(session, s_tok, u_tok)

        # Control: each patient's own bundle carries their own markers —
        # proves the markers are reachable at all before asserting absence.
        assert "MARKER-NOTE-A" in bundle_a.text
        assert "MARKER-DOC-A" in bundle_a.text
        assert "MARKER-NOTE-B" in bundle_b.text
        assert "MARKER-DOC-B" in bundle_b.text

        # Isolation: patient A's bundle never carries patient B's content
        # (and vice versa), text or per-document breakdown or manifest.
        assert "MARKER-NOTE-B" not in bundle_a.text, "patient B's note leaked into A's bundle"
        assert "MARKER-DOC-B" not in bundle_a.text, "patient B's document leaked into A's bundle"
        assert "MARKER-NOTE-A" not in bundle_b.text, "patient A's note leaked into B's bundle"
        assert "MARKER-DOC-A" not in bundle_b.text, "patient A's document leaked into B's bundle"

        doc_texts_a = {d.text for d in bundle_a.documents}
        doc_texts_b = {d.text for d in bundle_b.documents}
        assert not any("MARKER-NOTE-B" in t or "MARKER-DOC-B" in t for t in doc_texts_a)
        assert not any("MARKER-NOTE-A" in t or "MARKER-DOC-A" in t for t in doc_texts_b)

        included_ids_a = {
            entry.get("source_key"): entry for entry in bundle_a.manifest["sources_included"]
        }
        doc_manifest_a = included_ids_a.get("document_manifest")
        assert doc_manifest_a is not None
        assert doc_manifest_a["document_ids"] == expected_doc_ids_a


# ---------------------------------------------------------------------------
# Shared-clinician access — two distinct grantees on one patient see the
# same content; a non-grantee sees nothing.
# ---------------------------------------------------------------------------


class TestSharedClinicianAccess:
    def test_two_grantees_see_identical_bundle(self, engine: Engine, tenant_schema: str) -> None:
        clinician_1 = str(uuid.uuid4())
        clinician_2 = str(uuid.uuid4())
        patient_id = _seed_patient(engine, tenant_schema, grantees=[clinician_1, clinician_2])

        notes_repo, docs_repo, session, s_tok, u_tok = _make_repos(
            engine, tenant_schema, clinician_1
        )
        try:
            notes_repo.add(_make_note(patient_id, marker="SHARED-NOTE"), clinician_1)
            docs_repo.add(_make_document(patient_id, clinician_1, marker="SHARED-DOC"))
            session.commit()
        finally:
            _cleanup_tokens(session, s_tok, u_tok)

        notes_repo_1, docs_repo_1, session_1, s_tok_1, u_tok_1 = _make_repos(
            engine, tenant_schema, clinician_1
        )
        try:
            bundle_1 = _assemble(
                notes_repo_1, docs_repo_1, patient_id=patient_id, user_id=clinician_1
            )
        finally:
            _cleanup_tokens(session_1, s_tok_1, u_tok_1)

        notes_repo_2, docs_repo_2, session_2, s_tok_2, u_tok_2 = _make_repos(
            engine, tenant_schema, clinician_2
        )
        try:
            bundle_2 = _assemble(
                notes_repo_2, docs_repo_2, patient_id=patient_id, user_id=clinician_2
            )
        finally:
            _cleanup_tokens(session_2, s_tok_2, u_tok_2)

        assert "SHARED-NOTE" in bundle_1.text
        assert "SHARED-DOC" in bundle_1.text
        assert bundle_1.text == bundle_2.text, (
            "two clinicians sharing a grant on the same patient must see the "
            "identical assembled bundle"
        )
        assert {d.document_id for d in bundle_1.documents} == {
            d.document_id for d in bundle_2.documents
        }

    def test_non_grantee_gets_empty_bundle(self, engine: Engine, tenant_schema: str) -> None:
        owner = str(uuid.uuid4())
        stranger = str(uuid.uuid4())
        patient_id = _seed_patient(engine, tenant_schema, grantees=[owner])

        notes_repo, docs_repo, session, s_tok, u_tok = _make_repos(engine, tenant_schema, owner)
        try:
            notes_repo.add(_make_note(patient_id, marker="OWNER-ONLY-NOTE"), owner)
            docs_repo.add(_make_document(patient_id, owner, marker="OWNER-ONLY-DOC"))
            session.commit()

            # Control: the owner sees their own content.
            control = _assemble(notes_repo, docs_repo, patient_id=patient_id, user_id=owner)
            assert "OWNER-ONLY-NOTE" in control.text
        finally:
            _cleanup_tokens(session, s_tok, u_tok)

        notes_repo_s, docs_repo_s, session_s, s_tok_s, u_tok_s = _make_repos(
            engine, tenant_schema, stranger
        )
        try:
            bundle = _assemble(notes_repo_s, docs_repo_s, patient_id=patient_id, user_id=stranger)
        finally:
            _cleanup_tokens(session_s, s_tok_s, u_tok_s)

        assert bundle.text == ""
        assert bundle.documents == ()
        assert "OWNER-ONLY-NOTE" not in bundle.text
        assert "OWNER-ONLY-DOC" not in bundle.text


# ---------------------------------------------------------------------------
# Soft-delete bypass — a soft-deleted note/document must not survive into
# the bundle even though the requester has full access to the patient.
# ---------------------------------------------------------------------------


class TestSoftDeleteBypass:
    def test_soft_deleted_note_and_document_excluded(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        clinician = str(uuid.uuid4())
        patient_id = _seed_patient(engine, tenant_schema, grantees=[clinician])

        notes_repo, docs_repo, session, s_tok, u_tok = _make_repos(engine, tenant_schema, clinician)
        try:
            note = _make_note(patient_id, marker="DELETE-ME-NOTE")
            doc = _make_document(patient_id, clinician, marker="DELETE-ME-DOC")
            notes_repo.add(note, clinician)
            docs_repo.add(doc)
            session.commit()

            # Control: both are visible before soft-delete.
            before = _assemble(notes_repo, docs_repo, patient_id=patient_id, user_id=clinician)
            assert "DELETE-ME-NOTE" in before.text
            assert "DELETE-ME-DOC" in before.text

            notes_repo.delete(note.id, clinician)
            docs_repo.soft_delete(doc.id, clinician, _now())
            session.commit()

            after = _assemble(notes_repo, docs_repo, patient_id=patient_id, user_id=clinician)
        finally:
            _cleanup_tokens(session, s_tok, u_tok)

        assert "DELETE-ME-NOTE" not in after.text, "soft-deleted note must not appear in the bundle"
        assert "DELETE-ME-DOC" not in after.text, (
            "soft-deleted document must not appear in the bundle"
        )
        assert after.documents == ()


# ---------------------------------------------------------------------------
# SQL-filter presence — the load-bearing structural assertion. Proves the
# *compiled SQL* the repos emit for the bundler's read path carries an
# explicit patient_id predicate bound to the requested patient, independent
# of whether the returned rows happen to look correct.
# ---------------------------------------------------------------------------


class TestSqlFilterPresence:
    def test_notes_and_documents_queries_filter_by_patient_id(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        clinician = str(uuid.uuid4())
        patient_id = _seed_patient(engine, tenant_schema, grantees=[clinician])

        notes_repo, docs_repo, session, s_tok, u_tok = _make_repos(engine, tenant_schema, clinician)
        captured: list[tuple[str, dict]] = []

        def _capture(  # noqa: PLR0913 — fixed by SQLAlchemy's event signature
            conn: object,
            cursor: object,
            statement: str,
            parameters: Any,
            context: object,
            executemany: object,
        ) -> None:
            captured.append((statement, dict(parameters) if parameters else {}))

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            notes_repo.list_by_patient(patient_id, clinician)
            docs_repo.list_for_patient(patient_id, clinician)
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
            _cleanup_tokens(session, s_tok, u_tok)

        _assert_patient_id_filter(captured, table="notes", patient_id=patient_id)
        _assert_patient_id_filter(captured, table="patient_documents", patient_id=patient_id)


def _assert_patient_id_filter(
    captured: list[tuple[str, dict]], *, table: str, patient_id: str
) -> None:
    """Assert one of ``captured``'s statements reads ``table`` filtered by
    an explicit ``patient_id`` predicate bound to ``patient_id``.

    Matches ``<table>.patient_id = %(name)s`` in the compiled SQL (the
    psycopg2 pyformat SQLAlchemy renders binds as) and cross-checks the
    parameter dict for that statement so the assertion can't be satisfied
    by an unrelated column named ``patient_id`` elsewhere in the query
    (e.g. in the SELECT list) or a bind carrying the wrong value.
    """
    reads = [
        (stmt, params)
        for stmt, params in captured
        if re.search(rf"\bfrom\s+{table}\b", stmt, re.IGNORECASE)
    ]
    assert reads, f"no SELECT against {table!r} was captured — expected list_by_patient's query"

    for statement, params in reads:
        match = re.search(rf"{table}\.patient_id\s*=\s*%\((\w+)\)s", statement, re.IGNORECASE)
        assert match, (
            f"expected an explicit '{table}.patient_id = %(...)s' filter in the "
            f"compiled SQL; got: {statement!r}"
        )
        bound_key = match.group(1)
        assert params.get(bound_key) == patient_id, (
            f"the {table}.patient_id filter's bound parameter {bound_key!r} did not "
            f"carry the requested patient id — got {params.get(bound_key)!r}, "
            f"expected {patient_id!r} (statement: {statement!r})"
        )
