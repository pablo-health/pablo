#!/usr/bin/env python
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Move a pre-provisioning deployment onto its own practice schema.

    python backend/bin/migrate_default_practice.py --check    # pre-flight only
    python backend/bin/migrate_default_practice.py            # pre-flight, then migrate

**Nobody has to run this.** The migrate job runs it automatically after
``alembic upgrade head`` (see ``backend/bin/migrate.py``), which is where every
other schema change already happens: before the rollout, with the database in
front of it and its output in the log. A deployment that would lose a chart
fails there, with nothing deployed.

This command exists for the case where you want the numbers BEFORE a deploy
rather than during one. ``--check`` is read-only and answers "would this be
clean?" without changing anything, which is worth knowing on a database whose
history you are unsure of.

Running it at BOOT was considered and rejected — an irreversible rename in a
startup path, where nobody is watching and a refusal reads as a crashloop. Boot
instead refuses to serve an unmigrated deployment, which is now a backstop
rather than the mechanism.

Exit codes: 0 migrated or already migrated; 1 pre-flight found rows that would
become invisible (nothing changed); 2 usage or connection error.
"""

from __future__ import annotations

import argparse
import logging
import sys

#: Identities to name in the --check summary before eliding.
_MAX_LISTED = 5


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
        unresolvable_identities,
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

    # Reported separately from the row counts because it is a different kind of
    # problem with a different remedy: these identities are not lost data, they
    # are people who could not sign in — and unlike the row counts, the migration
    # FIXES this one by backfilling. So --check reports it without failing.
    stranded = unresolvable_identities(engine)
    if stranded:
        shown = ", ".join(stranded[:_MAX_LISTED]) + (" …" if len(stranded) > _MAX_LISTED else "")
        print(
            f"\n{len(stranded)} identity/identities currently resolve to no practice "
            f"({shown}).\nThe migration maps them onto the deployment's practice; "
            f"nothing to fix by hand."
        )

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
