#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cheap, Docker-free checks on the Alembic chain and the tenant template.

Three checks, all pure file and git inspection — no database, no container —
so this is fast enough to run before every push:

1. **Models ship with migrations** (CLAUDE.md guardrail #4). If a model file
   (``models.py`` / ``platform_models.py``) is in the diff, at least one ``A``
   (added) file under ``backend/alembic/versions/`` must be in it too.

2. **The chain has exactly one head.** ``down_revision`` makes the versions
   directory a linked list, so two branches cut from the same parent leave it
   with two heads. Git merges both without complaint and alembic then refuses
   to run at all. Nothing in a file-overlap review predicts this — the two
   migrations are two different files — so it has to be checked structurally.

3. **A migration touching a tenant table regenerates the template.**
   Provisioning applies ``tenant_template.sql``, not the chain, so a migration
   that lands without a regenerated template ships a column that exists for
   every migrated tenant and is missing from every newly provisioned one. That
   surfaces far from its cause, as ``column ... does not exist`` in whatever
   test provisions a fresh tenant.

Check 3 fires only for tables the template already defines, which is what keeps
it free of false positives on platform-only migrations — those never touch the
practice schema, so they legitimately leave the template alone. It is a
backstop for the common case, not a replacement for the ``Tenant template
matches alembic head`` CI job: that job regenerates for real and diffs, so it
also catches a brand-new table and a hand-edited template whose columns are in
the wrong order. Neither is visible here without a database.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_FILES: frozenset[str] = frozenset(
    {
        "backend/app/db/models.py",
        "backend/app/db/platform_models.py",
    }
)
MIGRATIONS_PREFIX = "backend/alembic/versions/"
TENANT_TEMPLATE = "backend/app/db/tenant_template.sql"

# ``revision`` / ``down_revision`` assignments in a migration module. Both are
# annotated (``revision: str = "abc"``), and ``down_revision`` may name several
# parents as a tuple on a merge revision, so parents are collected as every
# quoted string on the right-hand side.
_REVISION_RE = re.compile(r"""^revision(?:\s*:[^=]+)?\s*=\s*["']([^"']+)["']""", re.M)
_DOWN_REVISION_RE = re.compile(r"^down_revision(?:\s*:[^=]+)?\s*=\s*(.+)$", re.M)
_QUOTED_RE = re.compile(r"""["']([^"']+)["']""")

# Tables a migration operates on. A table name alone is not enough to decide:
# ``users`` names both a platform table and (historically) a per-tenant one, so
# an explicit schema qualification has to win over the bare name.
_NON_TENANT_SCHEMAS = frozenset({"platform", "public", "information_schema", "pg_catalog"})

_OP_CALL_RE = re.compile(
    r"""op\.(?:add_column|drop_column|alter_column|create_index|drop_index"""
    r"""|create_table|drop_table|create_foreign_key|create_unique_constraint)"""
    r"""\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
)
# ``schema="platform"`` passed to an op call.
_OP_SCHEMA_RE = re.compile(r"""schema\s*=\s*["']([A-Za-z_][A-Za-z0-9_]*)["']""")

# Raw SQL, capturing the schema qualifier when the statement carries one.
_SQL_RES = (
    re.compile(
        r"""\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?"""
        r"""(?:"?([A-Za-z_][A-Za-z0-9_]*)"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?""",
        re.I,
    ),
    re.compile(
        r"""\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"""
        r"""(?:"?([A-Za-z_][A-Za-z0-9_]*)"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?""",
        re.I,
    ),
    re.compile(
        r"""\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?"""
        r"""(?:"?([A-Za-z_][A-Za-z0-9_]*)"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?""",
        re.I,
    ),
)

# ``CREATE TABLE __TENANT_SCHEMA__.<name>`` in the generated template.
_TEMPLATE_TABLE_RE = re.compile(
    r"""CREATE\s+TABLE\s+__TENANT_SCHEMA__\.\s*"?([A-Za-z_][A-Za-z0-9_]*)"?""", re.I
)


def _fail(message: str) -> None:
    """Emit a failure block on stderr, in order with the stdout report.

    stdout is block-buffered when piped and stderr is not, so without the
    flush the failures land above the OK lines they belong beneath.
    """
    sys.stdout.flush()
    print(message, file=sys.stderr)
    sys.stderr.flush()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(result.stderr)
        sys.exit(2)
    return result.stdout


def _name_status(base: str | None, staged: bool) -> list[tuple[str, str]]:
    if staged:
        raw = _git("diff", "--cached", "--name-status")
    else:
        base = base or "origin/main"
        raw = _git("diff", "--name-status", f"{base}...HEAD")
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0][0]
        path = parts[-1]
        out.append((status, path))
    return out


def _load_chain() -> dict[str, tuple[list[str], str]]:
    """Map every revision in the working tree to ``(parents, filename)``."""
    versions = REPO_ROOT / MIGRATIONS_PREFIX
    chain: dict[str, tuple[list[str], str]] = {}
    for path in sorted(versions.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rev_match = _REVISION_RE.search(text)
        if not rev_match:
            continue
        down_match = _DOWN_REVISION_RE.search(text)
        parents = _QUOTED_RE.findall(down_match.group(1)) if down_match else []
        chain[rev_match.group(1)] = (parents, path.name)
    return chain


def _check_single_head(chain: dict[str, tuple[list[str], str]]) -> int:
    """Fail when the versions directory has more than one head."""
    if not chain:
        print("migration-lint: no migrations found - skipping chain check.")
        return 0

    referenced = {parent for parents, _ in chain.values() for parent in parents}
    heads = sorted(rev for rev in chain if rev not in referenced)

    if len(heads) == 1:
        print(f"migration-lint: OK - single alembic head ({heads[0]}, {chain[heads[0]][1]}).")
        return 0

    if not heads:
        _fail("migration-lint: FAIL - no alembic head; the chain has a cycle.")
        return 1

    _fail(
        f"migration-lint: FAIL - {len(heads)} alembic heads. Two branches were "
        "cut from the same parent, so the chain forked:"
    )
    for head in heads:
        _fail(f"  - {head}  ({chain[head][1]})")
    _fail(
        "\nRe-point the migration that landed second at the other head — landing\n"
        "order is chain order, so whoever merges last re-points:\n"
        '  down_revision: str | Sequence[str] | None = "<the other head>"\n'
        "Do NOT `alembic merge` to paper over it; that leaves a permanent fork in\n"
        "the graph for what is one line of down_revision.\n"
        "Re-run the tenant-template regen afterwards — it is captured at chain head."
    )
    return 1


def _tenant_tables() -> set[str]:
    """Table names the generated tenant template defines."""
    template = REPO_ROOT / TENANT_TEMPLATE
    if not template.exists():
        return set()
    text = template.read_text(encoding="utf-8", errors="replace")
    return {name.lower() for name in _TEMPLATE_TABLE_RE.findall(text)}


def _tables_touched(text: str) -> set[str]:
    """Tables a migration operates on, minus any explicitly not in a tenant schema.

    A bare table name is ambiguous — ``users`` has named both a platform table
    and a per-tenant one — so an explicit ``schema=`` kwarg or a schema-qualified
    raw statement is taken at its word and the table dropped from the result.
    """
    touched: set[str] = set()

    # alembic op calls: the schema kwarg, when present, sits in the same call, so
    # read from the match to the start of the next op call.
    matches = list(_OP_CALL_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        schema = _OP_SCHEMA_RE.search(text, match.end(), end)
        if schema and schema.group(1).lower() in _NON_TENANT_SCHEMAS:
            continue
        touched.add(match.group(1).lower())

    # Raw SQL: trust the qualifier when the statement carries one.
    for pattern in _SQL_RES:
        for schema, table in pattern.findall(text):
            if schema and schema.lower() in _NON_TENANT_SCHEMAS:
                continue
            touched.add(table.lower())

    return touched


def _check_template_regenerated(
    changes: list[tuple[str, str]], added_migrations: list[str]
) -> int:
    """Fail when a migration touches a tenant table but the template is untouched."""
    if not added_migrations:
        return 0

    if any(path == TENANT_TEMPLATE for status, path in changes if status in {"A", "M"}):
        print("migration-lint: OK - tenant template was regenerated alongside the migration(s).")
        return 0

    tenant_tables = _tenant_tables()
    if not tenant_tables:
        print("migration-lint: tenant template not found - skipping template check.")
        return 0

    offenders: list[tuple[str, list[str]]] = []
    for rel in added_migrations:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sorted(_tables_touched(text) & tenant_tables)
        if hits:
            offenders.append((rel, hits))

    if not offenders:
        print("migration-lint: OK - no added migration touches a table the tenant template defines.")
        return 0

    _fail(
        "migration-lint: FAIL - migration(s) change a tenant table but "
        f"{TENANT_TEMPLATE} is unchanged:"
    )
    for rel, hits in offenders:
        _fail(f"  - {rel}  (touches: {', '.join(hits)})")
    _fail(
        "\nProvisioning applies the template, not the chain, so without the regen the\n"
        "column exists for every migrated tenant and is missing from every newly\n"
        "provisioned one:\n"
        "  poetry run python backend/scripts/regen_tenant_template.py\n"
        "then commit the updated file. Generate it — do not hand-edit it. The dump\n"
        "reflects ALTER TABLE order, so a new column belongs at the END of its table,\n"
        "which is rarely where the model declares it."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        help="Base ref to diff against (default: origin/main).",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Check the staged index instead of HEAD vs base.",
    )
    args = parser.parse_args()

    changes = _name_status(args.base, args.staged)

    changed_models = [path for status, path in changes if path in MODEL_FILES and status in {"A", "M"}]
    added_migrations = [
        path
        for status, path in changes
        if path.startswith(MIGRATIONS_PREFIX)
        and status == "A"
        and path.endswith(".py")
        and not path.endswith("__init__.py")
    ]

    failed = 0

    # 1. Models ship with migrations (guardrail #4).
    if not changed_models:
        print("migration-lint: no model changes - nothing to check.")
    elif added_migrations:
        print(
            f"migration-lint: OK - {len(changed_models)} model file(s) changed, "
            f"{len(added_migrations)} new migration(s) added."
        )
        for m in changed_models:
            print(f"  model:     {m}")
        for m in added_migrations:
            print(f"  migration: {m}")
    else:
        _fail("migration-lint: FAIL - model files changed without a new migration:")
        for m in changed_models:
            _fail(f"  - {m}")
        _fail(
            "\nGenerate one in the same commit:\n"
            '  cd backend && poetry run alembic revision --autogenerate -m "<short description>"\n'
            "then review the emitted file under backend/alembic/versions/ before committing.\n"
            "See CLAUDE.md guardrail #4."
        )
        failed = 1

    # 2. The chain has exactly one head. Checked against the working tree rather
    #    than the diff: on a pull request the checkout is already main merged
    #    with the branch, which is the state a fork would actually break.
    failed |= _check_single_head(_load_chain())

    # 3. A migration touching a tenant table regenerates the template.
    failed |= _check_template_regenerated(changes, added_migrations)

    return failed


if __name__ == "__main__":
    sys.exit(main())
