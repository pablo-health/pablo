# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regenerate ``backend/app/db/tenant_template.sql`` from alembic head.

The tenant template SQL is the **canonical schema for a freshly-provisioned
tenant**. ``create_practice_schema`` applies this file (with the target
schema name substituted) instead of the old
``Base.metadata.create_all + enable_rls_on_schema``
pair — that pair silently dropped raw-SQL DDL (functions, triggers,
custom types) that lived only in alembic migrations, and bug PABLO-API-500
on the 2026-05-17 pentest was the consequence: fresh tenants were stamped
at alembic-head without ``has_patient_access`` ever being created in them.
Column shape evolution (the historical ``_migrate_practice_columns``
boot patch — VARCHAR datetime widening, late ``ADD COLUMN`` calls) now
lives in the alembic chain itself (revision ``b7de65c29385``), so a
regenerated dump faithfully reflects it.

This script:
  1. Brings up a disposable Postgres via testcontainers.
  2. Runs ``alembic upgrade head`` against it — same env.py the platform
     uses, so the result is whatever real prod migrations produce.
  3. ``pg_dump --schema=practice`` to capture every object that lives in
     the canonical tenant schema, including function bodies and indexes.
  4. Post-processes:
       * Strips ``CREATE SCHEMA practice`` (the caller creates the target
         schema).
       * Strips dump-prologue ``SET`` statements (transaction_timeout,
         client_encoding, etc.) — not relevant for applying to a live DB.
       * Strips ``DROP ... IF EXISTS`` statements pg_dump emits with
         ``--clean`` (we don't pass --clean; this is belt-and-suspenders).
       * Strips ownership / privilege noise (--no-owner --no-privileges
         handle most of this but a few ALTER ... OWNER lines slip through
         depending on pg_dump version).
       * Replaces every ``practice.`` qualification with the literal
         placeholder ``__TENANT_SCHEMA__`` — ``create_practice_schema``
         does the substitution at apply time.
       * Keeps the ``alembic_version`` row exactly as dumped, so applying
         the template both creates tables and stamps the schema at HEAD
         in a single SQL transaction. No separate ``_stamp_alembic_at_head``
         call is needed.
  5. Captures the row-level security a practice schema gets. The reference
     ``practice`` schema has none — ``enable_rls_on_schema`` skips it by
     name — so the script provisions a scratch practice schema from the
     template just captured, runs the real ``enable_rls_on_schema`` over it
     (the function itself, not a copy of its rules), dumps that schema, and
     keeps only its row-level security: every ``ENABLE`` / ``FORCE ROW
     LEVEL SECURITY`` and every policy the reconcile created, with the
     fingerprint comment it wrote. It then gives the scratch schema its
     practice directory access (``apply_practice_directory_access``), and the
     directory's read policies are captured from the same dump; they carry no
     fingerprint, because the reconcile does not manage them. The grants,
     pinned search paths and function ownership that go with those policies
     cannot come out of this dump (``--no-owner --no-privileges``), so they are
     written out by ``practice_directory.directory_access_sql`` and appended.
     The scratch schema's name is rewritten to the placeholder like
     ``practice.`` is above, and the script refuses to write a section that
     still names it.
  6. Writes the result to ``backend/app/db/tenant_template.sql``: the schema,
     then the marker line, then the row-level security section, which
     provisioning applies to practice schemas only.

Run::

    poetry run python backend/scripts/regen_tenant_template.py

CI should regenerate the file and ``git diff --exit-code`` to enforce
"template stays in sync with alembic head".
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

# Support both direct script execution and imports from the test suite.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.tenant_template_capture import (
    CAPTURE_SCHEMA,
    DIRECTORY_ACCESS_HEADING,
    ROW_SECURITY_MARKER,
    TENANT_SCHEMA_PLACEHOLDER,
    _post_process,
)

__all__ = [
    "CAPTURE_SCHEMA",
    "DIRECTORY_ACCESS_HEADING",
    "ROW_SECURITY_MARKER",
    "TENANT_SCHEMA_PLACEHOLDER",
    "_post_process",
]

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "app" / "db" / "tenant_template.sql"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _bring_up_postgres() -> tuple[str, PostgresContainer]:
    """Start a disposable Postgres container; return (url, container)."""
    # Reaper container fails on Docker Desktop on macOS; pytest config does
    # the same thing. Must be set before testcontainers is imported.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        "postgres:16-alpine",
        username="pablo",
        password="pablo_dev",  # noqa: S106
        dbname="pablo",
        driver=None,
    )
    container.start()
    return container.get_connection_url(), container


def _run_alembic_upgrade(database_url: str) -> None:
    """Apply ``alembic upgrade head`` against the target DB.

    Runs via ``poetry run`` in a subprocess so we use the same env the
    Makefile / CI runs, and so a failed migration produces a clean
    non-zero exit instead of a half-imported alembic state inside this
    Python process.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["DATABASE_BACKEND"] = "postgres"

    # Platform chain first. The tenant chain declares foreign keys into
    # ``platform.users`` and creates nothing in that schema itself, so it cannot
    # run against an empty database on its own — ``create_all`` in env.py used to
    # cover for that, and no longer does.
    for chain_args in (["-n", "platform", "upgrade", "head"], ["upgrade", "head"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *chain_args],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            raise SystemExit(f"alembic {' '.join(chain_args)} failed (exit {result.returncode})")


def _pg_dump_practice_schema(
    container: PostgresContainer, schema: str = "practice", *, comments: bool = False
) -> str:
    """``pg_dump --schema-only --schema=<schema>`` against the test DB.

    Runs ``pg_dump`` *inside* the postgres container via ``docker exec``
    so the client version always matches the server. The host's
    ``pg_dump`` is often older than the testcontainers postgres image
    and refuses to dump (``server version: 16.x; pg_dump version: 14.x``).

    ``--no-owner`` / ``--no-privileges`` strip ownership and GRANTs since
    fresh tenants run as the same role and don't need cross-role grants
    baked into the template. ``--no-comments`` keeps the file readable;
    the row-level security capture keeps comments, because the policy
    fingerprints are comments, and extracts only those.
    """
    container_id = container.get_wrapped_container().id
    cmd = [
        "docker",
        "exec",
        container_id,
        "pg_dump",
        "--schema-only",
        f"--schema={schema}",
        "--no-owner",
        "--no-privileges",
        *([] if comments else ["--no-comments"]),
        "--no-tablespaces",
        "--no-publications",
        "--no-subscriptions",
        "-U",
        "pablo",
        "pablo",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"pg_dump (in container) failed (exit {result.returncode})")
    return result.stdout


# Runs in a subprocess, from ``backend/``, so the app is imported the way the
# migrate step imports it and this script's own process never loads it.
_CAPTURE_PROVISION_SCRIPT = """
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

from sqlalchemy import create_engine

from app.db.practice_directory import apply_practice_directory_access, directory_access_sql
from app.db.provisioning import apply_template_sql, reconcile_practice_rls

url, schema, template_path, access_path, placeholder = sys.argv[1:6]
engine = create_engine(url)
try:
    apply_template_sql(engine, schema, Path(template_path).read_text())
    reconcile_practice_rls(engine, schema)
    with engine.begin() as conn:
        apply_practice_directory_access(conn, schema)
    Path(access_path).write_text(directory_access_sql(placeholder))
finally:
    engine.dispose()
"""


def _provision_capture_schema(database_url: str, schema_part: str) -> str:
    """Build the scratch practice schema the way provisioning builds one.

    ``schema_part`` is the template just captured from ``practice``, so the
    scratch schema has exactly the shape a newly provisioned practice has
    before its row-level security — and then gets that row-level security
    from the real ``enable_rls_on_schema``, and its practice directory access
    from the real ``apply_practice_directory_access``.

    Returns ``directory_access_sql`` for the schema placeholder: the part of
    that access the dump cannot carry.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["DATABASE_BACKEND"] = "postgres"
    with tempfile.TemporaryDirectory() as tmp:
        template_file = Path(tmp) / "schema_part.sql"
        template_file.write_text(schema_part)
        access_file = Path(tmp) / "directory_access.sql"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _CAPTURE_PROVISION_SCRIPT,
                database_url,
                CAPTURE_SCHEMA,
                str(template_file),
                str(access_file),
                TENANT_SCHEMA_PLACEHOLDER,
            ],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            raise SystemExit(f"provisioning the capture schema failed (exit {result.returncode})")
        return access_file.read_text()


def main() -> None:
    if shutil.which("pg_dump") is None:
        raise SystemExit(
            "pg_dump not found on PATH. Install Postgres client tools "
            "(brew install postgresql, apt-get install postgresql-client, etc.)."
        )

    container = None
    try:
        url, container = _bring_up_postgres()
        _run_alembic_upgrade(url)
        schema_part = _post_process(_pg_dump_practice_schema(container))
        directory_access = _provision_capture_schema(url, schema_part)
        capture = _pg_dump_practice_schema(container, CAPTURE_SCHEMA, comments=True)
    finally:
        if container is not None:
            container.stop()

    # Text only, so importing it here touches no database. Imported late so
    # this module stays importable without the app for its other helpers.
    from app.db.rls_capture import row_security_section

    try:
        row_security = row_security_section(capture, CAPTURE_SCHEMA, schema_part)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    template = (
        schema_part
        + "\n"
        + ROW_SECURITY_MARKER
        + "\n"
        + row_security
        + "\n"
        + DIRECTORY_ACCESS_HEADING
        + "\n"
        + directory_access
    )

    # Write to a temp file in the same directory and rename — atomic on
    # POSIX. Avoids leaving a half-written tenant_template.sql if the
    # script is killed.
    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=TEMPLATE_PATH.parent,
        delete=False,
        suffix=".sql.tmp",
    ) as tmp:
        tmp.write(template)
        tmp_path = Path(tmp.name)
    tmp_path.replace(TEMPLATE_PATH)

    print(f"Wrote {TEMPLATE_PATH} ({len(template):,} bytes)")


if __name__ == "__main__":
    main()
