# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail tests: route handlers must declare and call AuditService.

These enforce CLAUDE.md guardrail #1 (PHI access without an audit entry is a
HIPAA § 164.312(b) gap) in CI, so regressions don't require a human reviewer
to spot them.

The actual checks live in ``backend/scripts/check_route_audit.py`` — a
pure-stdlib AST script with no app/DB/network dependency — so the exact same
logic runs in CI (here), in a git pre-commit hook, and in a Claude Code
PostToolUse hook fired when a route file is edited. This test is a thin
delegator so the script and the CI gate can never drift.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_route_audit.py"
_spec = importlib.util.spec_from_file_location("check_route_audit", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guardrail)

_ALL_ROUTE_FILES = guardrail._route_files(None)


def test_no_underscore_prefixed_audit_or_http_request_params() -> None:
    violations = guardrail.underscore_param_violations(_ALL_ROUTE_FILES)
    assert not violations, (
        "Underscore-prefixed `_audit` / `_http_request` parameters in a route handler "
        "tell Python and every linter the value is intentionally unused — a silent "
        "bypass of guardrail #1. Rename to `audit` / `http_request` and call them, "
        "or remove the parameter entirely.\n\n" + "\n".join(violations)
    )


def test_routes_injecting_audit_service_must_call_it() -> None:
    violations = guardrail.injected_but_uncalled_violations(_ALL_ROUTE_FILES)
    assert not violations, (
        "Route handlers that inject AuditService must call it — otherwise the "
        "injection is dead weight and the PHI access is unaudited.\n\n" + "\n".join(violations)
    )


def test_every_route_audits_or_is_classified() -> None:
    """Fail-closed: every handler must inject+call the tenant AuditService, OR be
    a reviewed PHI-marker exemption, OR be an explicit non-PHI classification.
    An unrecognized route is a violation, not a silent pass."""
    violations = guardrail.fail_closed_audit_violations(_ALL_ROUTE_FILES)
    assert not violations, (
        "Every route handler must audit PHI access or be explicitly classified "
        "non-PHI. Inject `audit: AuditService` and log the access, or add "
        "(method, mounted_path) to AUDIT_EXEMPT_NON_PHI_ROUTES with a reason. A "
        "PHI-marker path may only go in the reviewed AUDIT_EXEMPT_PHI_ROUTES list. "
        "The platform PlatformAuditService does NOT satisfy this — PHI must hit the "
        "tenant AuditService.\n\n" + "\n".join(violations)
    )


def test_no_phi_marker_path_is_lazily_exempted() -> None:
    """Backstop: a PHI-marker path may never appear in AUDIT_EXEMPT_NON_PHI_ROUTES.
    Such a path must be audited, or — if genuinely metadata-only — go in the
    heavily-reviewed AUDIT_EXEMPT_PHI_ROUTES list instead."""
    violations = guardrail._exempt_config_violations()
    assert not violations, (
        "A PHI-marker path was lazily exempted in AUDIT_EXEMPT_NON_PHI_ROUTES.\n\n"
        + "\n".join(violations)
    )


def test_full_scan_is_clean() -> None:
    """End-to-end: the whole route surface passes every rule today."""
    assert not guardrail.find_violations(None)
