#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pre-commit gate for the route-audit guardrail (CLAUDE.md #1).

Delegates to the canonical check in ``backend/scripts/check_route_audit.py`` so
the local commit gate, CI (``backend/tests/test_route_audit_guardrails.py``),
the audit-coverage-check skill, and the PostToolUse hook all share one
implementation and can't drift. Blocks the commit (exit 1) if any PHI-touching
route handler skips AuditService, injects it but never calls it, or uses an
underscore-prefixed ``_audit`` / ``_http_request`` bypass.

Originally this only caught the underscore bypass; it now runs the full
canonical check, so the missing-audit class (e.g. an unaudited patient list)
is blocked locally too, not just in CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL = REPO_ROOT / "backend" / "scripts" / "check_route_audit.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("check_route_audit", _CANONICAL)
    if spec is None or spec.loader is None:
        print(f"cannot load canonical check: {_CANONICAL}", file=sys.stderr)
        return 2
    guardrail = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guardrail)
    return guardrail.main()


if __name__ == "__main__":
    sys.exit(main())
