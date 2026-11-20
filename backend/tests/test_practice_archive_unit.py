# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The parts of practice archiving that need no database.

The dump, verify, drop and restore run against Postgres in
``tests_integration/database/test_practice_archive.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.db.practice_archive import (
    ArchiveManifest,
    LocalDirectoryArchive,
    PgClientTools,
    PgConnection,
    PracticeArchiveError,
    _require_practice_schema_name,
    check_client_versions,
)
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from pathlib import Path

_TOOLS = PgClientTools(pg_dump="/x/pg_dump", pg_restore="/x/pg_restore")


def _manifest() -> ArchiveManifest:
    return ArchiveManifest(
        practice_id="p1",
        schema_name="practice_p1",
        alembic_revision="abc123",
        table_counts={"patients": 3, "notes": 0},
        dump_sha256="0" * 64,
        dump_bytes=10,
        pg_dump_version="16.4",
        server_version="16.4",
        archived_at="2026-11-20T00:00:00+00:00",
    )


class TestClientVersions:
    def test_matching_major_is_accepted(self) -> None:
        version = check_client_versions(_TOOLS, 160013, probe=lambda _path: "16.4")
        assert version == "16.4"

    def test_a_newer_pg_dump_is_refused(self) -> None:
        probe = {"/x/pg_dump": "17.2", "/x/pg_restore": "16.4"}.__getitem__
        with pytest.raises(
            PracticeArchiveError, match=r"pg_dump is version 17\.2 but the server is PostgreSQL 16"
        ):
            check_client_versions(_TOOLS, 160013, probe=probe)

    def test_an_older_pg_restore_is_refused(self) -> None:
        probe = {"/x/pg_dump": "16.4", "/x/pg_restore": "15.8"}.__getitem__
        with pytest.raises(PracticeArchiveError, match=r"pg_restore is version 15\.8"):
            check_client_versions(_TOOLS, 160013, probe=probe)

    def test_the_server_version_is_read_by_major(self) -> None:
        # 170000 is PostgreSQL 17.0; a 16 client is refused against it.
        with pytest.raises(PracticeArchiveError, match="server is PostgreSQL 17"):
            check_client_versions(_TOOLS, 170000, probe=lambda _path: "16.9")


class TestSchemaNames:
    @pytest.mark.parametrize("name", ["practice", "platform", "public", "practice_", "Practice_a"])
    def test_non_practice_schemas_are_refused(self, name: str) -> None:
        with pytest.raises((PracticeArchiveError, ValueError)):
            _require_practice_schema_name(name)

    @pytest.mark.parametrize(
        "name",
        [
            "practice_default",
            "practice_0f8e2c1a_9b7d_4e6f_a1b2_c3d4e5f60718",
            "practice_pentest_ab12cd34",
        ],
    )
    def test_generated_names_are_accepted(self, name: str) -> None:
        _require_practice_schema_name(name)


class TestManifest:
    def test_round_trips(self) -> None:
        manifest = _manifest()
        assert ArchiveManifest.from_json(manifest.to_json()) == manifest

    def test_an_unknown_format_is_refused(self) -> None:
        data = _manifest().to_json().replace(b'"format": 1', b'"format": 2')
        with pytest.raises(PracticeArchiveError, match="Unknown archive manifest format 2"):
            ArchiveManifest.from_json(data)

    def test_a_missing_field_is_refused(self) -> None:
        data = _manifest().to_json().replace(b'"dump_sha256"', b'"something_else"')
        with pytest.raises(PracticeArchiveError, match="missing or mistypes"):
            ArchiveManifest.from_json(data)

    def test_garbage_is_refused(self) -> None:
        with pytest.raises(PracticeArchiveError, match="not valid JSON"):
            ArchiveManifest.from_json(b"\x00not json")


class TestLocalDirectoryArchive:
    def test_writes_and_reads_back(self, tmp_path: Path) -> None:
        store = LocalDirectoryArchive(tmp_path / "a")
        store.write("practice_x.dump", b"bytes")
        assert store.read("practice_x.dump") == b"bytes"
        assert [p.name for p in (tmp_path / "a").iterdir()] == ["practice_x.dump"]

    @pytest.mark.parametrize("name", ["../escape", "a/b", "", ".."])
    def test_refuses_names_that_leave_the_directory(self, tmp_path: Path, name: str) -> None:
        with pytest.raises(ValueError, match="plain file name"):
            LocalDirectoryArchive(tmp_path).write(name, b"x")


class TestPgConnection:
    def test_from_engine_keeps_the_password_out_of_conninfo(self) -> None:
        engine = create_engine(
            "postgresql+psycopg2://app:s3cret@db.internal:6543/pablo?sslmode=require"
        )
        conn = PgConnection.from_engine(engine)

        conninfo = conn.conninfo()
        assert "s3cret" not in conninfo
        assert "dbname='pablo'" in conninfo
        assert "host='db.internal'" in conninfo
        assert "port='6543'" in conninfo
        assert "sslmode='require'" in conninfo
        assert conn.environ()["PGPASSWORD"] == "s3cret"
        assert "s3cret" not in repr(conn)

    def test_conninfo_can_name_another_database(self) -> None:
        conn = PgConnection(dbname="pablo", host="h")
        assert "dbname='scratch'" in conn.conninfo("scratch")

    def test_values_are_quoted(self) -> None:
        conn = PgConnection(dbname="it's", host="h")
        assert r"dbname='it\'s'" in conn.conninfo()

    def test_an_engine_with_no_address_is_refused(self) -> None:
        engine = create_engine("postgresql+psycopg2://", creator=lambda: None)
        with pytest.raises(PracticeArchiveError, match="Pass connection="):
            PgConnection.from_engine(engine)
