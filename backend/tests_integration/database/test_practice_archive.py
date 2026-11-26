# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Archive and restore one practice schema against a real Postgres.

The subject dumps every row of a practice, so it runs as a role that bypasses
row-level security: the container's superuser
(``INTEGRATION_SUPERUSER_DATABASE_URL``, exported by the conftest). The
practice itself is provisioned by the ordinary non-super role, so the
ownership a restore has to reproduce is the real one.

``pg_dump`` and ``pg_restore`` come from PATH and must match the server's
major version; the archive refuses otherwise, and so do these tests.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db import practice_archive as pa
from app.db.migrate_tenants import _alembic_config_for
from app.db.practice_archive import (
    ArchiveManifest,
    LocalDirectoryArchive,
    PracticeArchiveError,
    archive_practice_schema,
    dump_object_name,
    manifest_object_name,
    register_post_restore_hook,
    reset_post_restore_hooks,
    restore_practice_schema,
)
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema
from app.db.rls_apply import tenant_rls_differences
from app.db.rls_runtime import desired_rls
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_app_url = os.environ.get("DATABASE_URL", "")
_super_url = os.environ.get("INTEGRATION_SUPERUSER_DATABASE_URL", "")
pytestmark = [
    pytest.mark.skipif(
        not _app_url or os.environ.get("DATABASE_BACKEND") != "postgres",
        reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
    ),
    pytest.mark.skipif(
        not _super_url,
        reason=(
            "Archiving dumps every row of a practice, so it needs a role that bypasses "
            "row-level security. Set INTEGRATION_SUPERUSER_DATABASE_URL to a superuser "
            "address for the same database (the testcontainers path sets it)."
        ),
    ),
]

_PATIENTS = 3


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))


def _head() -> str:
    head = _script().get_current_head()
    assert head is not None
    return head


@pytest.fixture(scope="module")
def admin() -> Iterator[Engine]:
    eng = create_engine(_super_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def app_engine() -> Iterator[Engine]:
    eng = create_engine(_app_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def store(tmp_path: Path) -> LocalDirectoryArchive:
    return LocalDirectoryArchive(tmp_path / "archive")


@pytest.fixture(autouse=True)
def _no_hooks() -> Iterator[None]:
    reset_post_restore_hooks()
    yield
    reset_post_restore_hooks()


def _provision(
    app_engine: Engine, admin: Engine, *, at_revision: str | None = None
) -> tuple[str, str]:
    """A registered, ready practice with rows; built by the app role, as signup builds it."""
    practice_id = str(uuid.uuid4())
    schema = f"practice_{practice_id.replace('-', '_')}"
    create_practice_schema(app_engine, schema)
    if at_revision is not None:
        with app_engine.begin() as conn:
            conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            cfg = _alembic_config_for(schema)
            cfg.attributes["connection"] = conn
            cfg.attributes["version_table_schema"] = schema
            command.downgrade(cfg, at_revision)
    now = datetime.now(UTC)
    with admin.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {PLATFORM_SCHEMA}.practices"  # noqa: S608
                " (id, name, schema_name, owner_email, product, status, is_active,"
                "  created_at, is_pentest, provisioning_status)"
                " VALUES (:id, 'Archive test', :s, '', 'pablo', 'active', TRUE, :ts, FALSE,"
                "  'ready')"
            ),
            {"id": practice_id, "s": schema, "ts": now},
        )
        for i in range(_PATIENTS):
            conn.execute(
                text(
                    f"INSERT INTO {schema}.patients (id, first_name, last_name,"  # noqa: S608
                    " first_name_lower, last_name_lower, status, session_count,"
                    " created_at, updated_at)"
                    " VALUES (:id, 'Test', :ln, 'test', :ln, 'active', 0, :ts, :ts)"
                ),
                {"id": str(uuid.uuid4()), "ln": f"archive{i}", "ts": now},
            )
    return practice_id, schema


@pytest.fixture
def practices(app_engine: Engine, admin: Engine) -> Iterator[list[tuple[str, str]]]:
    made: list[tuple[str, str]] = []
    yield made
    with admin.begin() as conn:
        for practice_id, schema in made:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(
                text(f"DELETE FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"),  # noqa: S608
                {"id": practice_id},
            )


@pytest.fixture
def practice(
    app_engine: Engine, admin: Engine, practices: list[tuple[str, str]]
) -> tuple[str, str]:
    made = _provision(app_engine, admin)
    practices.append(made)
    return made


def _status(admin: Engine, practice_id: str) -> str:
    with admin.connect() as conn:
        return str(
            conn.execute(
                text(
                    f"SELECT provisioning_status FROM {PLATFORM_SCHEMA}.practices"  # noqa: S608
                    " WHERE id = :id"
                ),
                {"id": practice_id},
            ).scalar_one()
        )


def _schema_exists(admin: Engine, schema: str) -> bool:
    with admin.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT count(*) FROM pg_namespace WHERE nspname = :s"), {"s": schema}
            ).scalar_one()
        )


def _counts(admin: Engine, schema: str) -> dict[str, int]:
    with admin.connect() as conn:
        tables = pa._list_tables(conn, schema)
        return pa._table_counts(conn, schema, tables)


def _version(admin: Engine, schema: str) -> str:
    with admin.connect() as conn:
        return str(
            conn.execute(text(f"SELECT version_num FROM {schema}.alembic_version")).scalar_one()  # noqa: S608
        )


def _verify_databases(admin: Engine) -> list[str]:
    with admin.connect() as conn:
        rows = conn.execute(
            text("SELECT datname FROM pg_database WHERE datname LIKE 'pablo_archive_verify_%'")
        ).fetchall()
    return [r[0] for r in rows]


def _owners(admin: Engine, schema: str) -> set[str]:
    with admin.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relkind IN ('r', 'p')"
            ),
            {"s": schema},
        ).fetchall()
    return {r[0] for r in rows}


def _forced_rls_tables(admin: Engine, schema: str) -> int:
    with admin.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = :s AND c.relforcerowsecurity"
                ),
                {"s": schema},
            ).scalar_one()
        )


# What isolation is made of, beyond table owners: function owners and
# SECURITY DEFINER settings, ACLs on relations, functions and the schema,
# RLS flags, policies and constraints. pg_dump keeps table owners and the
# RLS flags by itself; the directory role's function ownership and its
# schema grant come back only through the RLS reconcile, so this is the
# check that notices the reconcile missing. Policies are compared by name
# and command, because pg_dump re-renders their expressions.
_CATALOG_SQL = """
SELECT 'rel ' || c.relname || ' kind=' || c.relkind::text
       || ' owner=' || pg_get_userbyid(c.relowner)
       || ' acl=' || coalesce(c.relacl::text, '-')
       || ' rls=' || c.relrowsecurity || '/' || c.relforcerowsecurity
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s
UNION ALL
SELECT 'fn ' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
       || ' owner=' || pg_get_userbyid(p.proowner) || ' secdef=' || p.prosecdef
       || ' acl=' || coalesce(p.proacl::text, '-') || ' cfg=' || coalesce(p.proconfig::text, '-')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = :s
UNION ALL
SELECT 'ns owner=' || pg_get_userbyid(nspowner) || ' acl=' || coalesce(nspacl::text, '-')
  FROM pg_namespace WHERE nspname = :s
UNION ALL
SELECT 'pol ' || tablename || '.' || policyname || ' ' || cmd
  FROM pg_policies WHERE schemaname = :s
UNION ALL
SELECT 'con ' || conrelid::regclass::text || '.' || conname || ' type=' || contype::text
       || ' valid=' || convalidated
  FROM pg_constraint k JOIN pg_namespace n ON n.oid = k.connamespace WHERE n.nspname = :s
"""


def _catalog(admin: Engine, schema: str) -> list[str]:
    with admin.connect() as conn:
        rows = conn.execute(text(_CATALOG_SQL), {"s": schema}).fetchall()
    return sorted(r[0] for r in rows)


def test_round_trip(admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive) -> None:
    practice_id, schema = practice
    before = _counts(admin, schema)
    owners_before = _owners(admin, schema)
    forced_before = _forced_rls_tables(admin, schema)
    catalog_before = _catalog(admin, schema)
    assert before["patients"] == _PATIENTS

    manifest = archive_practice_schema(admin, practice_id, schema, store)

    assert manifest.table_counts == before
    assert manifest.alembic_revision == _head()
    assert (store.directory / dump_object_name(schema)).stat().st_size == manifest.dump_bytes
    stored = ArchiveManifest.from_json(store.read(manifest_object_name(schema)))
    assert stored == manifest
    assert not _schema_exists(admin, schema)
    assert _status(admin, practice_id) == "archived"
    assert _verify_databases(admin) == []

    restored = restore_practice_schema(admin, practice_id, schema, store)

    assert restored == manifest
    assert _status(admin, practice_id) == "ready"
    assert _counts(admin, schema) == manifest.table_counts
    # Owned and isolated the way provisioning left it, not by the superuser.
    assert _owners(admin, schema) == owners_before
    assert _forced_rls_tables(admin, schema) == forced_before
    assert _catalog(admin, schema) == catalog_before


def test_restore_brings_an_older_policy_set_back_to_the_template(
    admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive
) -> None:
    practice_id, schema = practice
    with admin.begin() as conn:
        conn.execute(text(f'DROP POLICY rls_patient_access ON "{schema}".patients'))
        conn.execute(text(f'CREATE POLICY rls_patient_access ON "{schema}".patients USING (true)'))
        conn.execute(text(f'DROP POLICY rls_patient_self_read ON "{schema}".patients'))
        conn.execute(text(f'CREATE POLICY rls_stale_extra ON "{schema}".patients USING (true)'))
    archive_practice_schema(admin, practice_id, schema, store)
    restore_practice_schema(admin, practice_id, schema, store)
    with Session(admin) as s:
        assert tenant_rls_differences(s, schema, desired_rls()) == {}


def test_older_revision_is_migrated_to_head(
    app_engine: Engine,
    admin: Engine,
    practices: list[tuple[str, str]],
    store: LocalDirectoryArchive,
) -> None:
    head = _head()
    parent = _script().get_revision(head).down_revision
    assert isinstance(parent, str)
    practice_id, schema = _provision(app_engine, admin, at_revision=parent)
    practices.append((practice_id, schema))

    manifest = archive_practice_schema(admin, practice_id, schema, store)
    assert manifest.alembic_revision == parent

    restore_practice_schema(admin, practice_id, schema, store)

    assert _version(admin, schema) == head
    assert _counts(admin, schema)["patients"] == _PATIENTS
    assert _status(admin, practice_id) == "ready"


class _CorruptingStore(LocalDirectoryArchive):
    """Stores a dump with some of its bytes changed, as failing storage would."""

    def write(self, name: str, data: bytes) -> None:
        if name.endswith(".dump"):
            middle = len(data) // 2
            flipped = bytes(b ^ 0xFF for b in data[middle : middle + 64])
            data = data[:middle] + flipped + data[middle + 64 :]
        super().write(name, data)


def _assert_untouched(admin: Engine, practice_id: str, schema: str, before: dict[str, int]) -> None:
    assert _schema_exists(admin, schema)
    assert _counts(admin, schema) == before
    assert _status(admin, practice_id) == "ready"
    assert _verify_databases(admin) == []


def test_dump_corrupted_in_storage_fails_verify(
    admin: Engine, practice: tuple[str, str], tmp_path: Path
) -> None:
    practice_id, schema = practice
    before = _counts(admin, schema)

    with pytest.raises(PracticeArchiveError, match="does not match its manifest"):
        archive_practice_schema(admin, practice_id, schema, _CorruptingStore(tmp_path / "bad"))

    _assert_untouched(admin, practice_id, schema, before)


def test_corrupted_dump_fails_the_verify_restore(
    admin: Engine,
    practice: tuple[str, str],
    store: LocalDirectoryArchive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bytes changed before the checksum is taken: only restoring it can tell."""
    practice_id, schema = practice
    before = _counts(admin, schema)
    real_dump = pa._run_pg_dump

    def corrupting_dump(client: pa._Client, args: list[str]) -> None:
        real_dump(client, args)
        if f"--schema={schema}" in args:
            path = next(a.removeprefix("--file=") for a in args if a.startswith("--file="))
            with Path(path).open("r+b") as f:
                size = f.seek(0, 2)
                f.seek(size * 2 // 3)
                f.write(b"\0" * 256)

    monkeypatch.setattr(pa, "_run_pg_dump", corrupting_dump)

    with pytest.raises(PracticeArchiveError):
        archive_practice_schema(admin, practice_id, schema, store)

    _assert_untouched(admin, practice_id, schema, before)


def test_failed_restore_leaves_archived_and_no_schema(
    admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive
) -> None:
    practice_id, schema = practice
    archive_practice_schema(admin, practice_id, schema, store)

    def failing_hook(_engine: Engine, _schema: str) -> None:
        raise RuntimeError("deployment step failed")

    register_post_restore_hook(failing_hook)

    with pytest.raises(RuntimeError, match="deployment step failed"):
        restore_practice_schema(admin, practice_id, schema, store)

    assert not _schema_exists(admin, schema)
    assert _status(admin, practice_id) == "archived"

    # Nothing about the failed attempt stops a later one.
    reset_post_restore_hooks()
    restore_practice_schema(admin, practice_id, schema, store)
    assert _status(admin, practice_id) == "ready"


def test_restore_with_counts_not_matching_drops_what_it_built(
    admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive
) -> None:
    practice_id, schema = practice
    manifest = archive_practice_schema(admin, practice_id, schema, store)
    wrong = ArchiveManifest(
        **{**manifest.__dict__, "table_counts": {**manifest.table_counts, "patients": 99}}
    )
    store.write(manifest_object_name(schema), wrong.to_json())

    with pytest.raises(PracticeArchiveError, match="does not match the manifest"):
        restore_practice_schema(admin, practice_id, schema, store)

    assert not _schema_exists(admin, schema)
    assert _status(admin, practice_id) == "archived"


def test_restore_refuses_a_ready_practice(
    admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive
) -> None:
    practice_id, schema = practice
    before = _counts(admin, schema)
    archive_practice_schema(admin, practice_id, schema, store)
    restore_practice_schema(admin, practice_id, schema, store)

    with pytest.raises(PracticeArchiveError, match="'ready'; this needs it 'archived'"):
        restore_practice_schema(admin, practice_id, schema, store)

    _assert_untouched(admin, practice_id, schema, before)


def test_archive_refuses_a_role_under_row_security(
    app_engine: Engine, admin: Engine, practice: tuple[str, str], store: LocalDirectoryArchive
) -> None:
    practice_id, schema = practice
    before = _counts(admin, schema)

    with pytest.raises(PracticeArchiveError, match="BYPASSRLS or superuser"):
        archive_practice_schema(app_engine, practice_id, schema, store)

    _assert_untouched(admin, practice_id, schema, before)
    assert not store.directory.exists()


def test_archive_refuses_the_template_and_platform(
    admin: Engine, store: LocalDirectoryArchive
) -> None:
    for name in ("practice", "platform", "public"):
        with pytest.raises(PracticeArchiveError, match="not a practice schema name"):
            archive_practice_schema(admin, "default", name, store)


class _WriteProbingStore(LocalDirectoryArchive):
    """Tries a write to the practice while the archive holds it, from another connection."""

    def __init__(self, directory: Path, engine: Engine, schema: str) -> None:
        super().__init__(directory)
        self.engine = engine
        self.schema = schema
        self.outcome: str | None = None

    def write(self, name: str, data: bytes) -> None:
        if self.outcome is None:
            with self.engine.connect() as conn:
                conn.execute(text("SET lock_timeout = '500ms'"))
                try:
                    conn.execute(
                        text(f"UPDATE {self.schema}.patients SET session_count = 1")  # noqa: S608
                    )
                    self.outcome = "written"
                except OperationalError as exc:
                    self.outcome = type(exc.orig).__name__
                conn.rollback()
        super().write(name, data)


def test_writes_wait_while_the_archive_runs(
    admin: Engine, practice: tuple[str, str], tmp_path: Path
) -> None:
    """A write after the snapshot would be dropped with the schema; none can land."""
    practice_id, schema = practice
    store = _WriteProbingStore(tmp_path / "probe", admin, schema)

    archive_practice_schema(admin, practice_id, schema, store)

    assert store.outcome == "LockNotAvailable"
