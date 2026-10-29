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
     fingerprint comment it wrote. Policies it did not create (the practice
     directory's, which its own pass re-applies along with the function
     ownership no capture can carry) are left out. The scratch schema's name
     is rewritten to the placeholder like ``practice.`` is above, and the
     script refuses to write a section that still names it.
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
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TENANT_SCHEMA_PLACEHOLDER = "__TENANT_SCHEMA__"
#: The same line as ``app.db.provisioning.TEMPLATE_ROW_SECURITY_MARKER``;
#: spelled out rather than imported so the script reads the app only where
#: it has to. ``test_tenant_template_sections`` holds the two together.
ROW_SECURITY_MARKER = "-- @@ row-level security: applied to practice schemas only @@\n"
#: The scratch practice schema row-level security is captured from.
CAPTURE_SCHEMA = "practice_template_capture"
#: The comment ``enable_rls_on_schema`` writes on every policy it creates
#: (``app.db.rls_state.FINGERPRINT_PREFIX``).
FINGERPRINT_PREFIX = "rls-fingerprint-v1-"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "app" / "db" / "tenant_template.sql"
BACKEND_DIR = Path(__file__).resolve().parents[1]

# Prologue ``SET`` statements pg_dump emits — these are session-level
# tweaks for dump fidelity, not part of the schema, and applying them
# inside a production session is at best noise and at worst harmful.
_PROLOGUE_PREFIXES = (
    "SET statement_timeout",
    "SET lock_timeout",
    "SET idle_in_transaction_session_timeout",
    "SET client_encoding",
    "SET standard_conforming_strings",
    "SELECT pg_catalog.set_config",
    "SET check_function_bodies",
    "SET xmloption",
    "SET client_min_messages",
    "SET row_security",
    "SET default_tablespace",
    "SET default_table_access_method",
    "SET transaction_timeout",
)

_HEADER = """-- Auto-generated by backend/scripts/regen_tenant_template.py.
-- DO NOT EDIT MANUALLY.
--
-- Canonical schema for a freshly-provisioned tenant. Applied by
-- app.db.provisioning.create_practice_schema after the target schema
-- has been created. The placeholder __TENANT_SCHEMA__ is substituted
-- with the real schema name before execution.
--
-- The section after the row-level security marker near the end is what
-- enable_rls_on_schema gives a practice schema. Provisioning applies it to
-- practice schemas and not to the template schema itself.
--
-- Regenerate after any alembic revision that touches DDL:
--   poetry run python backend/scripts/regen_tenant_template.py
"""


def _bring_up_postgres() -> tuple[str, object]:
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


def _pg_dump_practice_schema(container, schema: str = "practice", *, comments: bool = False) -> str:
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


def _post_process(dump: str) -> str:
    """Turn raw pg_dump output into an apply-anywhere tenant template."""
    out_lines: list[str] = []
    for raw_line in dump.splitlines():
        line = raw_line
        stripped = line.strip()

        if not stripped:
            out_lines.append(line)
            continue
        if stripped.startswith("--"):
            # Drop pg_dump section banners (-- Name: ..., -- Data for ...);
            # they reference ``practice`` which would be misleading after
            # rewrite. Keep blank lines for readability.
            continue
        if stripped.startswith(_PROLOGUE_PREFIXES):
            continue
        if stripped.startswith("CREATE SCHEMA practice;") or stripped.startswith(
            "CREATE SCHEMA IF NOT EXISTS practice"
        ):
            # The caller creates the target schema with the substituted
            # name — don't recreate ``practice`` itself.
            continue
        if stripped.startswith("ALTER SCHEMA practice OWNER"):
            continue
        if stripped.startswith("\\restrict") or stripped.startswith("\\unrestrict"):
            # psql meta-commands emitted by pg_dump 17+ to fence the
            # dump against execution by older psql. Not valid SQL — must
            # be stripped before feeding to SQLAlchemy.
            continue

        # Replace every "practice." qualifier with the placeholder. Use a
        # regex anchored to a word boundary on the left so we don't match
        # ``my_practice.foo`` if any such identifier ever existed.
        line = re.sub(r"(?<![A-Za-z0-9_])practice\.", f"{TENANT_SCHEMA_PLACEHOLDER}.", line)

        out_lines.append(line)

    body = "\n".join(out_lines).rstrip() + "\n"
    return _HEADER + "\n" + body


# Runs in a subprocess, from ``backend/``, so the app is imported the way the
# migrate step imports it and this script's own process never loads it.
_CAPTURE_PROVISION_SCRIPT = """
import sys
from pathlib import Path

from sqlalchemy import create_engine

from app.db.provisioning import apply_template_sql, reconcile_practice_rls

url, schema, template_path = sys.argv[1:4]
engine = create_engine(url)
try:
    apply_template_sql(engine, schema, Path(template_path).read_text())
    reconcile_practice_rls(engine, schema)
finally:
    engine.dispose()
"""


def _provision_capture_schema(database_url: str, schema_part: str) -> None:
    """Build the scratch practice schema the way provisioning builds one.

    ``schema_part`` is the template just captured from ``practice``, so the
    scratch schema has exactly the shape a newly provisioned practice has
    before its row-level security — and then gets that row-level security
    from the real ``enable_rls_on_schema``.
    """
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["DATABASE_BACKEND"] = "postgres"
    with tempfile.TemporaryDirectory() as tmp:
        template_file = Path(tmp) / "schema_part.sql"
        template_file.write_text(schema_part)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _CAPTURE_PROVISION_SCRIPT,
                database_url,
                CAPTURE_SCHEMA,
                str(template_file),
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


_RLS_FLAG = re.compile(r"^ALTER TABLE (?:ONLY )?(\S+) (?:ENABLE|FORCE) ROW LEVEL SECURITY;$")
_POLICY_START = re.compile(r"^CREATE POLICY (\w+) ON (\S+) ")
_POLICY_COMMENT = re.compile(r"^COMMENT ON POLICY (\w+) ON (\S+) IS '([^']*)';$")


def _policies(sql: str) -> dict[tuple[str, str], str]:
    """Every ``CREATE POLICY`` statement in ``sql``, keyed on (policy, table).

    pg_dump writes one per entry, starting at column 0 and ending on the
    first line that ends with ``;``.
    """
    found: dict[tuple[str, str], str] = {}
    lines = sql.splitlines()
    i = 0
    while i < len(lines):
        match = _POLICY_START.match(lines[i])
        if not match:
            i += 1
            continue
        start = i
        while not lines[i].rstrip().endswith(";"):
            i += 1
        found[(match.group(1), match.group(2))] = "\n".join(lines[start : i + 1])
        i += 1
    return found


def _row_security_section(dump: str, schema: str, schema_part: str) -> str:
    """The row-level security statements of ``schema``'s dump, schema-neutral.

    Kept: every RLS flag, and every policy carrying a reconcile fingerprint
    together with that fingerprint. A policy the schema part already
    creates is not created twice: identical, only its fingerprint is added;
    different, it is dropped and replaced.
    """
    qualifier = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(schema)}\.")

    def neutral(sql: str) -> str:
        return qualifier.sub(f"{TENANT_SCHEMA_PLACEHOLDER}.", sql)

    fingerprinted: dict[tuple[str, str], str] = {}
    flags: list[str] = []
    for line in dump.splitlines():
        if _RLS_FLAG.match(line):
            flags.append(neutral(line))
            continue
        comment = _POLICY_COMMENT.match(line)
        if comment and comment.group(3).startswith(FINGERPRINT_PREFIX):
            fingerprinted[(comment.group(1), neutral(comment.group(2)))] = neutral(line)

    existing = _policies(schema_part)
    statements = list(flags)
    for (name, table), captured in _policies(dump).items():
        key = (name, neutral(table))
        fingerprint = fingerprinted.pop(key, None)
        if fingerprint is None:
            continue
        create = neutral(captured)
        if key not in existing:
            statements.append(create)
        elif existing[key] != create:
            statements.extend([f"DROP POLICY {name} ON {key[1]};", create])
        statements.append(fingerprint)
    if fingerprinted:
        raise SystemExit(f"fingerprints with no policy in the dump: {sorted(fingerprinted)}")

    section = "\n\n".join(statements) + "\n"
    if re.search(rf"(?<![A-Za-z0-9_]){re.escape(schema)}(?![A-Za-z0-9_])", section):
        raise SystemExit(
            f"the row-level security section still names {schema}; a policy would "
            "be pinned to the capture schema instead of the one being provisioned"
        )
    return section


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
        _provision_capture_schema(url, schema_part)
        capture = _pg_dump_practice_schema(container, CAPTURE_SCHEMA, comments=True)
    finally:
        if container is not None:
            container.stop()

    row_security = _row_security_section(capture, CAPTURE_SCHEMA, schema_part)
    template = schema_part + "\n" + ROW_SECURITY_MARKER + "\n" + row_security

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
