# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Archive one practice schema to a dump, and restore it again.

:func:`archive_practice_schema` writes a practice's schema to a ``pg_dump``
custom-format file, proves that file restores to the same rows, and only then
drops the schema and marks the practice ``archived``.
:func:`restore_practice_schema` is the reverse: it restores the dump under the
same schema name, checks the rows again, migrates the schema from the revision
it was archived at to the tenant chain's head, re-applies row-level security
and marks the practice ``ready``.

Which practice to archive, when, and where the dump is kept are the
deployment's decisions. The engine owns the dump format and the proof. Storage
is behind two small protocols, :class:`ArchiveSink` and :class:`ArchiveSource`;
:class:`LocalDirectoryArchive` implements both for tests and self-hosting.

What the proof is
=================

A dump and a manifest are written together. The manifest holds the schema
name, the tenant alembic revision read from the schema's ``alembic_version``,
a row count for every table, the dump's sha256, and the ``pg_dump`` and server
versions.

The counts and the dump come from one snapshot. The archive takes a ``SHARE``
lock on every table in the schema first, on a connection it holds until the
drop, so no write can land after the snapshot and be lost with the schema.
A second connection then opens a ``REPEATABLE READ`` transaction, exports its
snapshot, and counts every table in it; ``pg_dump --snapshot`` dumps from that
same snapshot. Because the locks are already held when the snapshot is taken,
every committed write is in it and no later one can exist.

The verify restores the dump AS STORED (read back from the sink, its sha256
checked against the manifest) into a throwaway database on the same server,
counts every table there and compares with the manifest. It never restores
over the live schema name. A practice schema refers to objects outside itself
(an extension an exclusion constraint needs, a policy that reads
``platform.practices``), so the throwaway database first receives the
structure, and no rows, of everything in the source database except practice
schemas. Roles are cluster-wide and need nothing.

Any mismatch or error raises :class:`PracticeArchiveError` and leaves the live
schema and the practice's status exactly as they were.

What the running role needs
===========================

Every practice table forces row-level security, so a role subject to it would
dump and count zero rows. ``pg_dump`` refuses rather than doing that, and so
does this module: the engine's role must be a superuser or ``BYPASSRLS`` (and
able to read the practice's tables). Archiving also needs ``CREATEDB`` for the
throwaway database; without it the archive refuses, rather than falling back
to a weaker check. Objects must end up owned by the role that owns practice
schemas, as provisioning leaves them, so the role must also be able to
``SET ROLE`` to that owner. All of this is checked before anything changes.

``pg_dump`` and ``pg_restore`` must have the server's major version. They
connect with the engine's own address (its URL), the password passed through
``PGPASSWORD`` rather than the command line. An engine built with a connection
creator has no such address; pass ``connection=`` explicitly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ..utcnow import utc_now
from . import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA, _validate_schema_name
from .migrate_tenants import _alembic_config_for, _alembic_head
from .platform_models import SCHEMA_READY
from .practice_directory import apply_practice_directory_access

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

#: ``provisioning_status`` of a practice whose schema lives in a dump.
SCHEMA_ARCHIVED = "archived"

#: Bumped when the manifest's shape changes; a restore refuses a format it
#: does not know rather than guessing.
MANIFEST_FORMAT = 1

#: A practice schema's name: ``practice_`` and a generated suffix. Narrower
#: than :func:`_validate_schema_name`, which also admits ``platform``,
#: ``public`` and the provisioning template ``practice`` — none of which this
#: module may ever drop.
_PRACTICE_SCHEMA_RE = re.compile(r"^practice_[a-z0-9][a-z0-9_]{0,53}$")

#: ``pg_dump (PostgreSQL) 16.16`` and the distribution-suffixed variants.
_CLIENT_VERSION_RE = re.compile(r"\(PostgreSQL\)\s+(\d+)(?:\.(\d+))?")

#: How long the archive waits for the table locks, and ``pg_dump`` for its own.
#: Archiving is for an idle practice; one with writes in flight past this is
#: not idle, and the archive gives up rather than queue behind them.
_LOCK_WAIT = "30s"


class PracticeArchiveError(RuntimeError):
    """An archive or restore refused, or failed and changed nothing it should not have."""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class ArchiveSink(Protocol):
    """Where an archive writes. It reads back too, so the verify proves the stored copy."""

    def write(self, name: str, data: bytes) -> None: ...

    def read(self, name: str) -> bytes: ...


class ArchiveSource(Protocol):
    """Where a restore reads from."""

    def read(self, name: str) -> bytes: ...


class LocalDirectoryArchive:
    """An :class:`ArchiveSink` and :class:`ArchiveSource` over one local directory.

    Writes go to a temporary file in the same directory and are renamed into
    place, so a reader never sees half a dump.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, name: str) -> Path:
        if "/" in name or name in {"", ".", ".."}:
            msg = f"Archive object name must be a plain file name, got {name!r}"
            raise ValueError(msg)
        return self.directory / name

    def write(self, name: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._path(name)
        partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
        partial.write_bytes(data)
        partial.replace(target)

    def read(self, name: str) -> bytes:
        return self._path(name).read_bytes()


def dump_object_name(schema_name: str) -> str:
    """The name a practice's dump is stored under."""
    return f"{schema_name}.dump"


def manifest_object_name(schema_name: str) -> str:
    """The name a practice's manifest is stored under, beside its dump."""
    return f"{schema_name}.manifest.json"


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveManifest:
    """What a dump holds, written beside it and checked on every restore."""

    practice_id: str
    schema_name: str
    alembic_revision: str
    table_counts: dict[str, int]
    dump_sha256: str
    dump_bytes: int
    pg_dump_version: str
    server_version: str
    archived_at: str
    format: int = field(default=MANIFEST_FORMAT)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), indent=2, sort_keys=True).encode()

    @classmethod
    def from_json(cls, data: bytes) -> ArchiveManifest:
        try:
            raw = json.loads(data)
        except ValueError as exc:
            msg = "The archive manifest is not valid JSON"
            raise PracticeArchiveError(msg) from exc
        if not isinstance(raw, dict) or raw.get("format") != MANIFEST_FORMAT:
            got = raw.get("format") if isinstance(raw, dict) else None
            msg = f"Unknown archive manifest format {got!r}; this engine reads {MANIFEST_FORMAT}"
            raise PracticeArchiveError(msg)
        try:
            return cls(
                practice_id=str(raw["practice_id"]),
                schema_name=str(raw["schema_name"]),
                alembic_revision=str(raw["alembic_revision"]),
                table_counts={str(k): int(v) for k, v in raw["table_counts"].items()},
                dump_sha256=str(raw["dump_sha256"]),
                dump_bytes=int(raw["dump_bytes"]),
                pg_dump_version=str(raw["pg_dump_version"]),
                server_version=str(raw["server_version"]),
                archived_at=str(raw["archived_at"]),
                format=int(raw["format"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            msg = f"The archive manifest is missing or mistypes a field: {exc}"
            raise PracticeArchiveError(msg) from exc


# ---------------------------------------------------------------------------
# Client tools and their connection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PgConnection:
    """A libpq address for ``pg_dump`` / ``pg_restore``, the password kept out of argv."""

    dbname: str
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    #: Further libpq keywords (``sslmode``, a socket ``host``, ...).
    options: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_engine(cls, engine: Engine) -> PgConnection:
        url = engine.url
        if not url.database:
            msg = (
                "The engine has no database address in its URL (it connects through a "
                "creator), so pg_dump cannot reach the same database. Pass connection= "
                "with a libpq address for it."
            )
            raise PracticeArchiveError(msg)
        options = {
            key: value if isinstance(value, str) else value[-1] for key, value in url.query.items()
        }
        return cls(
            dbname=url.database,
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password if isinstance(url.password, str) else None,
            options=options,
        )

    def conninfo(self, dbname: str | None = None) -> str:
        """A libpq keyword string, with no password in it."""
        pairs: dict[str, str] = {**self.options, "dbname": dbname or self.dbname}
        if self.host:
            pairs["host"] = self.host
        if self.port:
            pairs["port"] = str(self.port)
        if self.user:
            pairs["user"] = self.user
        return " ".join(f"{key}={_libpq_quote(value)}" for key, value in pairs.items())

    def environ(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.password is not None:
            env["PGPASSWORD"] = self.password
        return env

    def sqlalchemy_url(self, dbname: str) -> URL:
        return URL.create(
            "postgresql+psycopg2",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=dbname,
            query=self.options,
        )


def _libpq_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


@dataclass(frozen=True)
class PgClientTools:
    """Paths to the client programs the archive runs."""

    pg_dump: str
    pg_restore: str

    @classmethod
    def discover(cls) -> PgClientTools:
        found = {name: shutil.which(name) for name in ("pg_dump", "pg_restore")}
        missing = sorted(name for name, path in found.items() if path is None)
        if missing:
            msg = (
                f"{' and '.join(missing)} not found on PATH. Archiving a practice needs the "
                "PostgreSQL client programs at the server's major version."
            )
            raise PracticeArchiveError(msg)
        return cls(pg_dump=str(found["pg_dump"]), pg_restore=str(found["pg_restore"]))


@dataclass(frozen=True)
class _Client:
    """The client programs and the address they connect with."""

    tools: PgClientTools
    conn: PgConnection


type VersionProbe = Callable[[str], str]


def client_version(program: str) -> str:
    """``X.Y`` as the program reports it with ``--version``."""
    result = subprocess.run(  # noqa: S603 — a path from PgClientTools, fixed arguments
        [program, "--version"], capture_output=True, text=True, check=True, timeout=30
    )
    match = _CLIENT_VERSION_RE.search(result.stdout)
    if match is None:
        msg = f"Could not read a PostgreSQL version from {program!r}: {result.stdout.strip()!r}"
        raise PracticeArchiveError(msg)
    return f"{match.group(1)}.{match.group(2) or 0}"


def check_client_versions(
    tools: PgClientTools, server_version_num: int, probe: VersionProbe = client_version
) -> str:
    """Refuse unless pg_dump and pg_restore have the server's major version.

    A newer ``pg_dump`` writes archives an older ``pg_restore`` cannot read,
    and an older one cannot dump a newer server at all; mixing majors is how a
    dump that cannot be restored gets written. Returns pg_dump's version.
    """
    server_major = server_version_num // 10000
    versions = {name: probe(path) for name, path in asdict(tools).items()}
    for name, version in versions.items():
        major = int(version.split(".", 1)[0])
        if major != server_major:
            msg = (
                f"{name} is version {version} but the server is PostgreSQL {server_major}. "
                f"Install the PostgreSQL {server_major} client programs; a dump taken or "
                "restored across major versions is refused."
            )
            raise PracticeArchiveError(msg)
    return versions["pg_dump"]


# ---------------------------------------------------------------------------
# Post-restore hooks
# ---------------------------------------------------------------------------

type PostRestoreHook = Callable[["Engine", str], None]

_post_restore_hooks: list[PostRestoreHook] = []


def register_post_restore_hook(hook: PostRestoreHook) -> None:
    """Run ``hook(engine, schema_name)`` after a restored schema reaches the tenant head.

    For a deployment that keeps its own per-tenant objects beside the engine's:
    the hook runs after the engine's chain and before row-level security is
    re-applied and the practice is marked ``ready``, so nothing reaches the
    schema before the deployment's part of it is current too. A hook that
    raises fails the restore: the schema is dropped and the practice stays
    ``archived``. The hook runs as the engine's role; objects it creates are
    owned by whatever role it sets.
    """
    _post_restore_hooks.append(hook)


def reset_post_restore_hooks() -> None:
    """Clear all registered hooks. For tests."""
    _post_restore_hooks.clear()


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


def archive_practice_schema(  # noqa: PLR0913 — the four the call needs, two keyword-only overrides
    engine: Engine,
    practice_id: str,
    schema_name: str,
    sink: ArchiveSink,
    *,
    connection: PgConnection | None = None,
    tools: PgClientTools | None = None,
) -> ArchiveManifest:
    """Dump one practice schema, prove the dump restores, then drop the schema.

    On success the dump and manifest are in ``sink``, the schema is gone, and
    the practice's ``provisioning_status`` is ``archived`` (set in the same
    transaction as the drop). On any failure nothing in the database has
    changed; whatever was already written to ``sink`` stays there.
    """
    _require_practice_schema_name(schema_name)
    client = _Client(
        tools or PgClientTools.discover(), connection or PgConnection.from_engine(engine)
    )
    server_num, server_version = _server_version(engine)
    pg_dump_version = check_client_versions(client.tools, server_num)
    owner = _schema_owner(engine, schema_name)
    if owner is None:
        msg = f"Schema '{schema_name}' does not exist; nothing to archive"
        raise PracticeArchiveError(msg)
    _require_role(engine, owner=owner, need_createdb=True)
    _require_practice(engine, practice_id, schema_name, SCHEMA_READY)

    with engine.connect() as hold:
        hold.execute(text("SELECT set_config('lock_timeout', :t, true)"), {"t": _LOCK_WAIT})
        _disable_session_timeouts(hold)
        hold.execute(text("SELECT pg_advisory_xact_lock(hashtext(:s))"), {"s": schema_name})
        _lock_practice_row(hold, practice_id, schema_name, SCHEMA_READY)
        locked = _list_tables(hold, schema_name)
        if not locked:
            msg = f"Schema '{schema_name}' has no tables; refusing to archive it"
            raise PracticeArchiveError(msg)
        # SHARE blocks every writer and lets readers through. Taken before the
        # snapshot below exists, so the snapshot holds every committed write
        # and none can follow it.
        qualified = ", ".join(f"{_ident(schema_name)}.{_ident(t)}" for t in locked)
        hold.execute(text(f"LOCK TABLE {qualified} IN SHARE MODE"))

        with tempfile.TemporaryDirectory(prefix="practice-archive-") as tmp:
            workdir = Path(tmp)
            dump_path = workdir / "schema.dump"
            preamble_path = workdir / "preamble.dump"
            counts, revision = _snapshot_dump(engine, client, schema_name, locked, workdir)
            data = dump_path.read_bytes()
            manifest = ArchiveManifest(
                practice_id=practice_id,
                schema_name=schema_name,
                alembic_revision=revision,
                table_counts=counts,
                dump_sha256=hashlib.sha256(data).hexdigest(),
                dump_bytes=len(data),
                pg_dump_version=pg_dump_version,
                server_version=server_version,
                archived_at=utc_now().isoformat(),
            )
            sink.write(dump_object_name(schema_name), data)
            sink.write(manifest_object_name(schema_name), manifest.to_json())
            stored = sink.read(dump_object_name(schema_name))
            _check_sha(stored, manifest)
            stored_path = workdir / "stored.dump"
            stored_path.write_bytes(stored)
            _verify_in_throwaway_database(engine, client, manifest, stored_path, preamble_path)

        # A table created in the schema after the locks were taken holds rows
        # no dump has seen; refuse rather than drop it.
        if _list_tables(hold, schema_name) != locked:
            msg = f"Tables in '{schema_name}' changed during the archive; nothing was dropped"
            raise PracticeArchiveError(msg)
        updated = hold.execute(
            text(
                f"UPDATE {PLATFORM_SCHEMA}.practices"  # noqa: S608 — constant schema name
                " SET provisioning_status = :archived"
                " WHERE id = :id AND schema_name = :s AND provisioning_status = :ready"
            ),
            {
                "archived": SCHEMA_ARCHIVED,
                "id": practice_id,
                "s": schema_name,
                "ready": SCHEMA_READY,
            },
        )
        if updated.rowcount != 1:
            msg = f"Practice '{practice_id}' changed status during the archive; nothing dropped"
            raise PracticeArchiveError(msg)
        # As the schema's owner, who may drop it whoever owns what is inside.
        hold.execute(text(f"SET LOCAL ROLE {_ident(owner)}"))
        hold.execute(text(f"DROP SCHEMA {_ident(schema_name)} CASCADE"))
        hold.commit()

    logger.info(
        "Archived practice schema '%s' (practice %s): %d tables, %d rows, %d bytes",
        schema_name,
        practice_id,
        len(manifest.table_counts),
        sum(manifest.table_counts.values()),
        manifest.dump_bytes,
    )
    return manifest


def _snapshot_dump(
    engine: Engine, client: _Client, schema_name: str, locked: list[str], workdir: Path
) -> tuple[dict[str, int], str]:
    """Count, read the revision and dump, all from one exported snapshot.

    Writes ``schema.dump`` (the practice) and ``preamble.dump`` (what it refers
    to) into ``workdir``.
    """
    with engine.connect() as snap_base:
        snap = snap_base.execution_options(isolation_level="REPEATABLE READ")
        _disable_session_timeouts(snap)
        snapshot_id = snap.execute(text("SELECT pg_export_snapshot()")).scalar_one()
        if _list_tables(snap, schema_name) != locked:
            msg = f"Tables in '{schema_name}' changed before the snapshot; nothing was dropped"
            raise PracticeArchiveError(msg)
        counts = _table_counts(snap, schema_name, locked)
        revision = _alembic_revision(snap, schema_name)
        _run_pg_dump(
            client,
            [
                f"--snapshot={snapshot_id}",
                f"--schema={schema_name}",
                f"--file={workdir / 'schema.dump'}",
            ],
        )
        # Everything outside the practice schemas, structure only: what the
        # dump refers to, and no rows of any practice.
        _run_pg_dump(
            client,
            [
                f"--snapshot={snapshot_id}",
                "--schema-only",
                f"--exclude-schema={DEFAULT_PRACTICE_SCHEMA}",
                f"--exclude-schema={DEFAULT_PRACTICE_SCHEMA}_*",
                f"--file={workdir / 'preamble.dump'}",
            ],
        )
        snap.rollback()
    return counts, revision


def _run_pg_dump(client: _Client, args: list[str]) -> None:
    _run(
        [
            client.tools.pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--lock-wait-timeout={_LOCK_WAIT}",
            *args,
            f"--dbname={client.conn.conninfo()}",
        ],
        client.conn,
        "pg_dump",
    )


def _run_pg_restore(
    client: _Client, dbname: str, dump_path: Path, *, role: str | None = None
) -> None:
    args = [
        client.tools.pg_restore,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--single-transaction",
        f"--dbname={client.conn.conninfo(dbname)}",
    ]
    if role is not None:
        args.append(f"--role={role}")
    args.append(str(dump_path))
    _run(args, client.conn, "pg_restore")


def _run(args: list[str], conn_params: PgConnection, what: str) -> None:
    result = subprocess.run(  # noqa: S603 — fixed program paths and validated names
        args, env=conn_params.environ(), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        detail = _redacted_stderr(result.stderr, what)
        msg = f"{what} failed (exit {result.returncode}): {detail}"
        raise PracticeArchiveError(msg)


# Words that introduce an object name in a PostgreSQL or pg_dump message
# (``table "patients"``, ``constraint "x_check"``). A quoted string after
# anything else may be a value, as in ``invalid input syntax for type
# integer: "..."``, and is replaced.
_OBJECT_WORDS = (
    "table|relation|constraint|schema|role|user|database|index|function|column|type"
    "|extension|sequence|view|policy|trigger|operator|collation|domain|server|file"
)
_QUOTED = re.compile(r"(?P<word>\b(?:" + _OBJECT_WORDS + r') )?"[^"]*"', re.IGNORECASE)


def _redacted_stderr(stderr: str, program: str) -> str:
    """The parts of a failed tool's stderr that are safe to put in an exception.

    The exception is meant to be logged, and stderr can carry rows: a COPY
    that fails a constraint prints the row in ``DETAIL: Failing row
    contains (...)`` and again as the raw COPY line in ``CONTEXT``, and a
    unique or exclusion failure prints ``DETAIL: Key (...)=(...)``. Only the
    program's own lines are kept (``pg_restore: error: ...``), and within
    them any quoted string that is not an object name is replaced.
    """
    kept: list[str] = []
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line.startswith(f"{program}:"):
            continue
        kept.append(_QUOTED.sub(lambda m: m.group(0) if m.group("word") else '"<redacted>"', line))
    return " | ".join(kept)[-2000:] or "(no diagnostic lines from the program)"


def _verify_in_throwaway_database(
    engine: Engine,
    client: _Client,
    manifest: ArchiveManifest,
    dump_path: Path,
    preamble_path: Path,
) -> None:
    """Restore the stored dump into a scratch database and compare it with the manifest."""
    scratch = f"pablo_archive_verify_{uuid.uuid4().hex[:12]}"
    with engine.connect() as admin_base:
        admin = admin_base.execution_options(isolation_level="AUTOCOMMIT")
        admin.execute(text(f"CREATE DATABASE {_ident(scratch)} TEMPLATE template0"))
    failure: BaseException | None = None
    try:
        _run_pg_restore(client, scratch, preamble_path)
        _run_pg_restore(client, scratch, dump_path)
        scratch_engine = create_engine(client.conn.sqlalchemy_url(scratch), poolclass=NullPool)
        try:
            with scratch_engine.connect() as conn:
                conn.execute(text("SET row_security = off"))
                tables = _list_tables(conn, manifest.schema_name)
                counts = _table_counts(conn, manifest.schema_name, tables)
                revision = _alembic_revision(conn, manifest.schema_name)
        finally:
            scratch_engine.dispose()
        _compare(manifest, counts, revision, "the verify restore")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            with engine.connect() as admin_base:
                admin = admin_base.execution_options(isolation_level="AUTOCOMMIT")
                admin.execute(text(f"DROP DATABASE IF EXISTS {_ident(scratch)} WITH (FORCE)"))
        except Exception as exc:
            # The scratch database holds a full copy of the practice. Leaving
            # it behind silently is not acceptable, so a failed drop fails the
            # archive (before the live schema is touched) unless the archive
            # is already failing for a reason of its own.
            logger.exception("Could not drop the archive verify database '%s'", scratch)
            if failure is None:
                msg = (
                    f"The archive verified, but its scratch database '{scratch}' could not be "
                    "dropped and holds a copy of the practice. Drop it by hand; the live "
                    "schema was not touched."
                )
                raise PracticeArchiveError(msg) from exc


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def restore_practice_schema(  # noqa: PLR0913 — the four the call needs, two keyword-only overrides
    engine: Engine,
    practice_id: str,
    schema_name: str,
    source: ArchiveSource,
    *,
    connection: PgConnection | None = None,
    tools: PgClientTools | None = None,
) -> ArchiveManifest:
    """Restore an archived practice schema and migrate it to the tenant chain's head.

    Refuses unless the practice is ``archived`` and the schema does not exist.
    On success the schema is back under its name, at the tenant head, with
    row-level security applied the way provisioning applies it, and the
    practice is ``ready``. On failure the half-restored schema is dropped and
    the practice stays ``archived``.
    """
    _require_practice_schema_name(schema_name)
    client = _Client(
        tools or PgClientTools.discover(), connection or PgConnection.from_engine(engine)
    )
    server_num, _ = _server_version(engine)
    check_client_versions(client.tools, server_num)
    owner = _schema_owner(engine, DEFAULT_PRACTICE_SCHEMA)
    if owner is None:
        msg = f"The provisioning template schema '{DEFAULT_PRACTICE_SCHEMA}' does not exist"
        raise PracticeArchiveError(msg)
    _require_role(engine, owner=owner, need_createdb=False)

    manifest = ArchiveManifest.from_json(source.read(manifest_object_name(schema_name)))
    if manifest.schema_name != schema_name or manifest.practice_id != practice_id:
        msg = (
            f"The manifest is for practice '{manifest.practice_id}' schema "
            f"'{manifest.schema_name}', not practice '{practice_id}' schema '{schema_name}'"
        )
        raise PracticeArchiveError(msg)
    data = source.read(dump_object_name(schema_name))
    _check_sha(data, manifest)

    # The same lock provisioning and schema drops take, held for the whole
    # restore so neither can run against a half-built schema.
    with engine.connect() as lock_conn:
        lock_conn.execute(text("SELECT pg_advisory_lock(hashtext(:s))"), {"s": schema_name})
        lock_conn.commit()
        try:
            _require_practice(engine, practice_id, schema_name, SCHEMA_ARCHIVED)
            if _schema_owner(engine, schema_name) is not None:
                msg = (
                    f"Schema '{schema_name}' already exists; refusing to restore over it. "
                    "It was not touched."
                )
                raise PracticeArchiveError(msg)
            try:
                _restore_locked(engine, client, manifest, data, owner)
            except BaseException:
                _drop_half_restored(engine, schema_name, owner)
                raise
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:s))"), {"s": schema_name})
            lock_conn.commit()

    logger.info(
        "Restored practice schema '%s' (practice %s): %d tables, %d rows, from revision %s",
        schema_name,
        practice_id,
        len(manifest.table_counts),
        sum(manifest.table_counts.values()),
        manifest.alembic_revision,
    )
    return manifest


def _restore_locked(
    engine: Engine, client: _Client, manifest: ArchiveManifest, data: bytes, owner: str
) -> None:
    schema_name = manifest.schema_name
    with tempfile.TemporaryDirectory(prefix="practice-restore-") as tmp:
        dump_path = Path(tmp) / "schema.dump"
        dump_path.write_bytes(data)
        # As the practice-schema owner, so the restored objects are owned the
        # way provisioning leaves them.
        _run_pg_restore(client, client.conn.dbname, dump_path, role=owner)

    with engine.connect() as conn:
        conn.execute(text("SET LOCAL row_security = off"))
        tables = _list_tables(conn, schema_name)
        counts = _table_counts(conn, schema_name, tables)
        revision = _alembic_revision(conn, schema_name)
        conn.rollback()
    _compare(manifest, counts, revision, "the restore")

    _upgrade_to_head(engine, schema_name, owner, revision)
    for hook in _post_restore_hooks:
        hook(engine, schema_name)
    _reconcile_rls_as(engine, schema_name, owner)

    with engine.begin() as conn:
        updated = conn.execute(
            text(
                f"UPDATE {PLATFORM_SCHEMA}.practices"  # noqa: S608 — constant schema name
                " SET provisioning_status = :ready"
                " WHERE id = :id AND schema_name = :s AND provisioning_status = :archived"
            ),
            {
                "ready": SCHEMA_READY,
                "id": manifest.practice_id,
                "s": schema_name,
                "archived": SCHEMA_ARCHIVED,
            },
        )
        if updated.rowcount != 1:
            msg = f"Practice '{manifest.practice_id}' changed status during the restore"
            raise PracticeArchiveError(msg)


def _upgrade_to_head(engine: Engine, schema_name: str, owner: str, current: str) -> None:
    head = _alembic_head()
    if head is None:
        msg = "The tenant chain has no head revision"
        raise PracticeArchiveError(msg)
    if current == head:
        return
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL ROLE {_ident(owner)}"))
        conn.execute(text(f"SET LOCAL search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema_name)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema_name
        command.upgrade(cfg, "head")
    logger.info("Migrated restored schema '%s' from %s to %s", schema_name, current, head)


def _reconcile_rls_as(engine: Engine, schema_name: str, owner: str) -> None:
    """Make a restored schema's row-level security match the templates, as its owner.

    The dump carries the policies the practice had when it was archived, which
    can be older than the templates now. Lenient, as the deploy-time pass is
    over a live schema: a restored schema can hold tables put there some other
    way, and those keep the policies they were dumped with.

    The session commits, which would end a ``SET LOCAL ROLE``, so the role is
    set for the connection and reset before it goes back to the pool.
    """
    from .rls_runtime import apply_practice_rls

    with engine.connect() as conn:
        conn.execute(text(f"SET ROLE {_ident(owner)}"))
        conn.commit()
        try:
            with Session(bind=conn) as session:
                session.execute(text(f"SET search_path = {schema_name}, {PLATFORM_SCHEMA}, public"))
                apply_practice_rls(session, schema_name, strict=False)
                # The dump keeps neither owners nor grants, so the directory
                # functions come back owned by whoever restored them and the
                # directory role holds nothing. Nothing else puts that back.
                apply_practice_directory_access(session, schema_name)
                session.commit()
        finally:
            conn.rollback()
            conn.execute(text("RESET ROLE"))
            conn.execute(text("RESET search_path"))
            conn.commit()


def _drop_half_restored(engine: Engine, schema_name: str, owner: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL ROLE {_ident(owner)}"))
            conn.execute(text(f"DROP SCHEMA IF EXISTS {_ident(schema_name)} CASCADE"))
    except Exception:
        logger.exception("Could not drop the half-restored schema '%s'", schema_name)


# ---------------------------------------------------------------------------
# Shared checks and reads
# ---------------------------------------------------------------------------


def _require_practice_schema_name(schema_name: str) -> None:
    _validate_schema_name(schema_name)
    if not _PRACTICE_SCHEMA_RE.match(schema_name):
        msg = f"'{schema_name}' is not a practice schema name; refusing"
        raise PracticeArchiveError(msg)


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _server_version(engine: Engine) -> tuple[int, str]:
    with engine.connect() as conn:
        num = int(conn.execute(text("SHOW server_version_num")).scalar_one())
        version = str(conn.execute(text("SHOW server_version")).scalar_one())
    return num, version


def _schema_owner(engine: Engine, schema_name: str) -> str | None:
    with engine.connect() as conn:
        owner = conn.execute(
            text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :s"),
            {"s": schema_name},
        ).scalar()
    return None if owner is None else str(owner)


def _require_role(engine: Engine, *, owner: str, need_createdb: bool) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user, rolsuper, rolbypassrls, rolcreatedb,"
                " pg_has_role(current_user, :owner, 'MEMBER')"
                " FROM pg_roles WHERE rolname = current_user"
            ),
            {"owner": owner},
        ).one()
    role, is_super, bypass, createdb, member = row
    problems = []
    if not (is_super or bypass):
        problems.append(
            "it is subject to row-level security, so it would dump and count no rows "
            "(it needs BYPASSRLS or superuser)"
        )
    if need_createdb and not (is_super or createdb):
        problems.append(
            "it cannot CREATE DATABASE, which the verify restore needs; the archive will "
            "not fall back to a weaker check (it needs CREATEDB)"
        )
    if not member:
        problems.append(f"it cannot SET ROLE to '{owner}', who owns practice schemas")
    if problems:
        msg = f"The database role '{role}' cannot archive or restore a practice: " + "; ".join(
            problems
        )
        raise PracticeArchiveError(msg)


def _require_practice(engine: Engine, practice_id: str, schema_name: str, status: str) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT schema_name, provisioning_status FROM {PLATFORM_SCHEMA}.practices"  # noqa: S608
                " WHERE id = :id"
            ),
            {"id": practice_id},
        ).one_or_none()
    _check_practice_row(
        None if row is None else (str(row[0]), str(row[1])), practice_id, schema_name, status
    )


def _lock_practice_row(conn: Connection, practice_id: str, schema_name: str, status: str) -> None:
    row = conn.execute(
        text(
            f"SELECT schema_name, provisioning_status FROM {PLATFORM_SCHEMA}.practices"  # noqa: S608
            " WHERE id = :id FOR UPDATE"
        ),
        {"id": practice_id},
    ).one_or_none()
    _check_practice_row(
        None if row is None else (str(row[0]), str(row[1])), practice_id, schema_name, status
    )


def _check_practice_row(
    row: tuple[str, str] | None, practice_id: str, schema_name: str, status: str
) -> None:
    if row is None:
        msg = f"No practice '{practice_id}' is registered"
        raise PracticeArchiveError(msg)
    registered_schema, current = row
    if registered_schema != schema_name:
        msg = (
            f"Practice '{practice_id}' is registered to schema '{registered_schema}', "
            f"not '{schema_name}'"
        )
        raise PracticeArchiveError(msg)
    if current != status:
        msg = f"Practice '{practice_id}' is '{current}'; this needs it '{status}'"
        raise PracticeArchiveError(msg)


def _disable_session_timeouts(conn: Connection) -> None:
    """The dump and verify can outlast the app's statement and idle timeouts.

    The connections here sit idle in a transaction while ``pg_dump`` and
    ``pg_restore`` run; a server-side idle timeout would end the transaction
    and take its locks or snapshot with it. The archive would then fail
    safely, but it would fail every time on a practice large enough.
    """
    conn.execute(text("SELECT set_config('statement_timeout', '0', true)"))
    conn.execute(text("SELECT set_config('idle_in_transaction_session_timeout', '0', true)"))


def _list_tables(conn: Connection, schema_name: str) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = :s AND c.relkind IN ('r', 'p') ORDER BY c.relname"
        ),
        {"s": schema_name},
    ).fetchall()
    return [str(r[0]) for r in rows]


def _table_counts(conn: Connection, schema_name: str, tables: list[str]) -> dict[str, int]:
    """Exact row counts. ``row_security = off`` makes a role under RLS fail, not undercount."""
    conn.execute(text("SELECT set_config('row_security', 'off', true)"))
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            conn.execute(
                text(f"SELECT count(*) FROM {_ident(schema_name)}.{_ident(table)}")  # noqa: S608
            ).scalar_one()
        )
    return counts


def _alembic_revision(conn: Connection, schema_name: str) -> str:
    rows = conn.execute(
        text(f"SELECT version_num FROM {_ident(schema_name)}.alembic_version")  # noqa: S608
    ).fetchall()
    if len(rows) != 1:
        msg = f"Schema '{schema_name}' has {len(rows)} alembic_version rows; expected one"
        raise PracticeArchiveError(msg)
    return str(rows[0][0])


def _check_sha(data: bytes, manifest: ArchiveManifest) -> None:
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest.dump_sha256 or len(data) != manifest.dump_bytes:
        msg = (
            f"The stored dump for '{manifest.schema_name}' does not match its manifest "
            f"(sha256 {digest[:12]}…, {len(data)} bytes; manifest says "
            f"{manifest.dump_sha256[:12]}…, {manifest.dump_bytes} bytes)"
        )
        raise PracticeArchiveError(msg)


def _compare(manifest: ArchiveManifest, counts: dict[str, int], revision: str, where: str) -> None:
    if revision != manifest.alembic_revision:
        msg = (
            f"{where} of '{manifest.schema_name}' is at revision {revision}; the manifest "
            f"says {manifest.alembic_revision}"
        )
        raise PracticeArchiveError(msg)
    if counts != manifest.table_counts:
        differing = sorted(
            t
            for t in set(counts) | set(manifest.table_counts)
            if counts.get(t) != manifest.table_counts.get(t)
        )
        detail = ", ".join(
            f"{t}: {counts.get(t, 'missing')} vs {manifest.table_counts.get(t, 'missing')}"
            for t in differing[:10]
        )
        msg = f"{where} of '{manifest.schema_name}' does not match the manifest ({detail})"
        raise PracticeArchiveError(msg)
