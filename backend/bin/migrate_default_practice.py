#!/usr/bin/env python
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move a pre-provisioning deployment onto its own practice schema.

    python backend/bin/migrate_default_practice.py --check    # pre-flight only
    python backend/bin/migrate_default_practice.py            # pre-flight, then migrate

Explicit rather than automatic, deliberately. The alternative — migrating at
boot — would run an irreversible schema rename inside a startup path, where
the operator is not watching, cannot answer a question, and a refusal reads as
a crashloop. An explicit command can be run twice, can be run with ``--check``
first, and puts a person in front of the numbers before any chart moves.

Boot refuses to serve an unmigrated deployment and names this command, so
skipping it is not something anyone can do by accident.

Exit codes: 0 migrated or already migrated; 1 pre-flight found rows that would
become invisible (nothing changed); 2 usage or connection error.
"""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_default_practice",
        description="Move this deployment's charts from the template schema onto practice_default.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the pre-flight and report; change nothing. Exits non-zero if any row "
        "would become invisible.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Migrate even though rows would become invisible. Only after reading the "
        "report and deciding those rows are genuinely disposable.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.db import get_engine  # noqa: PLC0415 — settings load on import
    from app.db.single_practice_migration import (  # noqa: PLC0415
        PreflightError,
        format_report,
        is_migrated,
        migrate,
        preflight,
    )

    try:
        engine = get_engine()
    except Exception as exc:
        print(f"Could not connect to the database: {exc}", file=sys.stderr)
        return 2

    if is_migrated(engine):
        print("Already migrated: this deployment has its own practice schema.")
        return 0

    counts = preflight(engine)
    print(format_report(counts))

    lost = sum(c.orphaned for c in counts)
    if args.check:
        if lost:
            print(
                f"\n{lost} row(s) would be readable by nobody once policies apply. "
                "Fix the data before migrating.",
                file=sys.stderr,
            )
            return 1
        print("\nPre-flight clean — safe to migrate.")
        return 0

    try:
        migrate(engine, force=args.force)
    except PreflightError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print("\nMigrated. This deployment now runs on its own practice schema with RLS applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
