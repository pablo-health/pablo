#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Copy the intake visibility fixture table into the front-end test tree.

Whether a question is shown is decided twice — once in Python, because the
server is authoritative about what a form still needs, and once in
TypeScript, because the portal has to know what to draw before it asks.
Two implementations of one rule is a thing that drifts, so they are held to
one table of cases rather than to two sets of tests that agree today.

The table lives in the backend test tree and is copied here rather than
imported, because the front end has to be buildable and testable on its own
— a vitest run that reached up into ``backend/`` would break the moment the
front end was built in a container of its own. So: one original, one copy,
and a test that fails the moment they differ
(``backend/tests/test_intake_visibility_fixture_sync.py``).

Run it after editing the table::

    python scripts/sync_intake_visibility_fixtures.py

``--check`` reports whether the copy is current and writes nothing, which
is what a hook or a local pre-push would want.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The original, edited by hand.
SOURCE = REPO_ROOT / "backend" / "tests" / "fixtures" / "intake_visibility_cases.json"

#: The copy, written by this script and never edited by hand.
DESTINATION = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "lib"
    / "intake"
    / "__tests__"
    / "fixtures"
    / "intake_visibility_cases.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the copy is current; write nothing",
    )
    args = parser.parse_args(argv)

    original = SOURCE.read_bytes()
    current = DESTINATION.read_bytes() if DESTINATION.exists() else None

    if current == original:
        print(f"{DESTINATION.relative_to(REPO_ROOT)} is current.")
        return 0

    if args.check:
        print(
            f"{DESTINATION.relative_to(REPO_ROOT)} is out of date — run "
            "python scripts/sync_intake_visibility_fixtures.py",
            file=sys.stderr,
        )
        return 1

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_bytes(original)
    print(f"Wrote {DESTINATION.relative_to(REPO_ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
