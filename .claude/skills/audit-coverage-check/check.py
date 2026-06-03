#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Report PHI-touching route handlers that skip AuditService.

Thin wrapper over the canonical guardrail in
``backend/scripts/check_route_audit.py`` so this on-demand skill, the CI test
(``backend/tests/test_route_audit_guardrails.py``), the pre-commit gate
(``scripts/check_audit_params.py``), and the PostToolUse hook all share one
implementation and cannot drift. Renders the canonical findings as markdown
and exits 1 when anything is flagged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL = REPO_ROOT / "backend" / "scripts" / "check_route_audit.py"


def _load_canonical():
    spec = importlib.util.spec_from_file_location("check_route_audit", _CANONICAL)
    if spec is None or spec.loader is None:
        print(f"cannot load canonical check: {_CANONICAL}", file=sys.stderr)
        sys.exit(2)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    guardrail = _load_canonical()
    violations = guardrail.find_violations()
    if not violations:
        print("audit-coverage-check: clean - every PHI route injects and calls AuditService.")
        return 0

    print("# Audit Coverage Violations\n")
    for v in violations:
        print(f"- {v}")
    print(
        "\n## Fix\n\n"
        "Inject `audit: AuditService = Depends(get_audit_service)` and log the access "
        "(e.g. `audit.log_<action>(...)`). If the route is genuinely non-PHI despite "
        "its path, add `(method, mounted_path)` to `AUDIT_EXEMPT_PHI_ROUTES` in "
        "`backend/scripts/check_route_audit.py` with a comment. See CLAUDE.md guardrail #1."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
