#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Fail if SQLAlchemy models changed without a matching new Alembic migration.

Enforces CLAUDE.md guardrail #4. Uses ``git diff --name-status`` to list
changed files between two refs (default: ``origin/main...HEAD``), or the
staged index with ``--staged``. If any model file (``models.py`` /
``platform_models.py``) is in the diff, at least one ``A`` (added)
file under ``backend/alembic/versions/`` must also be in the diff.
"""

from __future__ import annotations

import argparse
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


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
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

    if not changed_models:
        print("migration-lint: no model changes - nothing to check.")
        return 0

    if added_migrations:
        print(
            f"migration-lint: OK - {len(changed_models)} model file(s) changed, "
            f"{len(added_migrations)} new migration(s) added."
        )
        for m in changed_models:
            print(f"  model:     {m}")
        for m in added_migrations:
            print(f"  migration: {m}")
        return 0

    print("migration-lint: FAIL - model files changed without a new migration:", file=sys.stderr)
    for m in changed_models:
        print(f"  - {m}", file=sys.stderr)
    print(
        "\nGenerate one in the same commit:\n"
        "  cd backend && poetry run alembic revision --autogenerate -m \"<short description>\"\n"
        "then review the emitted file under backend/alembic/versions/ before committing.\n"
        "See CLAUDE.md guardrail #4.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
