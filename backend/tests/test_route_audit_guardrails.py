# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail tests: route handlers must declare and call AuditService.

These tests enforce CLAUDE.md guardrail #1 (PHI access without an audit entry
is a HIPAA § 164.312(b) gap) in CI, so regressions don't require a human
reviewer to spot them.

Three rules:

1. No route handler may prefix an ``audit`` or ``http_request`` parameter
   with an underscore. Python's ``_`` prefix means "intentionally unused"
   and silences every linter — exactly the regression pattern we want to
   block. If audit/http_request are not needed, the route probably isn't
   PHI-touching and should be reviewed by a human.

2. Any route handler that injects ``audit: AuditService`` must actually
   call ``audit.<something>(...)`` in its body.

3. Any route handler whose path matches a known PHI marker must inject
   ``AuditService``.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES_DIR = Path(__file__).resolve().parent.parent / "app" / "routes"

# Path substrings that signal a route touches PHI or PHI-adjacent data.
# Extend this list whenever a new PHI surface is added.
PHI_PATH_MARKERS: tuple[str, ...] = (
    "/patients",
    "/sessions",
    "/appointments",
    "/transcript",
    "/audio",
    "/soap",
    "/notes",
    "/chat",
    "/resolve-client",
    "/import-clients",
)

FORBIDDEN_UNDERSCORE_PARAMS: frozenset[str] = frozenset({"_audit", "_http_request"})

HTTP_METHODS: frozenset[str] = frozenset({"get", "post", "patch", "put", "delete"})


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map ``router``/``*_router`` binding names to their ``APIRouter(prefix=...)``.

    Without this the path matched against PHI markers is the decorator's
    literal arg (e.g. ``/conversations``), missing the prefix from
    ``APIRouter(prefix="/api/chat")`` and silently exempting whole route
    surfaces from the PHI-marker check.
    """
    prefixes: dict[str, str] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Call):
            continue
        callee = stmt.value.func
        callee_name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else None
        )
        if callee_name != "APIRouter":
            continue
        prefix = ""
        for kw in stmt.value.keywords:
            if (
                kw.arg == "prefix"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                prefix = kw.value.value
                break
        for target in stmt.targets:
            if isinstance(target, ast.Name) and (
                target.id == "router" or target.id.endswith("_router")
            ):
                prefixes[target.id] = prefix
    return prefixes


def _iter_route_handlers() -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, Path]]:
    """Return (full_path, method, function_node, file) for every ``@router.<method>`` handler.

    ``full_path`` includes the router prefix so PHI-marker matching sees
    the URL FastAPI actually exposes.
    """
    handlers: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef, Path]] = []
    for py_file in sorted(ROUTES_DIR.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text())
        prefixes = _router_prefixes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                if not isinstance(dec.func, ast.Attribute):
                    continue
                if not isinstance(dec.func.value, ast.Name):
                    continue
                router_name = dec.func.value.id
                if router_name != "router" and not router_name.endswith("_router"):
                    continue
                if dec.func.attr not in HTTP_METHODS:
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    continue
                path = dec.args[0].value
                if not isinstance(path, str):
                    continue
                full_path = prefixes.get(router_name, "") + path
                handlers.append((full_path, dec.func.attr, node, py_file))
    return handlers


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [arg.arg for arg in (*func.args.args, *func.args.kwonlyargs)]


def _param_annotations(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    result: dict[str, str] = {}
    for arg in (*func.args.args, *func.args.kwonlyargs):
        if arg.annotation is not None:
            result[arg.arg] = ast.unparse(arg.annotation)
    return result


def _calls_audit(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function body contains any ``audit.<attr>(...)`` call."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "audit":
            return True
    return False


def test_no_underscore_prefixed_audit_or_http_request_params() -> None:
    violations: list[str] = []
    for path, method, func, py_file in _iter_route_handlers():
        for name in _param_names(func):
            if name in FORBIDDEN_UNDERSCORE_PARAMS:
                violations.append(
                    f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                    f"declares forbidden parameter `{name}`"
                )
    assert not violations, (
        "Underscore-prefixed `_audit` / `_http_request` parameters in a route handler "
        "tell Python and every linter the value is intentionally unused — a silent "
        "bypass of guardrail #1. Rename to `audit` / `http_request` and call them, "
        "or remove the parameter entirely.\n\n" + "\n".join(violations)
    )


def test_routes_injecting_audit_service_must_call_it() -> None:
    violations: list[str] = []
    for path, method, func, py_file in _iter_route_handlers():
        annotations = _param_annotations(func)
        if not any("AuditService" in ann for ann in annotations.values()):
            continue
        if not _calls_audit(func):
            violations.append(
                f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                f"injects AuditService but never calls audit.*"
            )
    assert not violations, (
        "Route handlers that inject AuditService must call it — otherwise the "
        "injection is dead weight and the PHI access is unaudited.\n\n" + "\n".join(violations)
    )


def test_phi_routes_inject_audit_service() -> None:
    violations: list[str] = []
    for path, method, func, py_file in _iter_route_handlers():
        if not any(marker in path for marker in PHI_PATH_MARKERS):
            continue
        annotations = _param_annotations(func)
        if not any("AuditService" in ann for ann in annotations.values()):
            violations.append(
                f"{py_file.name}::{func.name} ({method.upper()} {path}) "
                f"matches a PHI path marker but does not inject AuditService"
            )
    assert not violations, (
        "Routes whose path contains a PHI marker "
        f"({', '.join(PHI_PATH_MARKERS)}) must inject `audit: AuditService` "
        "and log the access. If the route is genuinely non-PHI despite the "
        "path, add it to an explicit allowlist in this test with a comment.\n\n"
        + "\n".join(violations)
    )


def test_router_prefix_is_resolved() -> None:
    """Without prefix resolution, ``APIRouter(prefix="/api/chat")`` paths
    like ``/conversations`` don't match any PHI marker, silently
    exempting whole route surfaces from ``test_phi_routes_inject_audit_service``.
    """
    source = (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/api/chat', tags=['chat'])\n"
        "patient_router = APIRouter(prefix='/api/patients')\n"
        "@router.get('/conversations')\n"
        "def handler():\n    ...\n"
        "@patient_router.get('/{patient_id}/extras')\n"
        "def patient_handler():\n    ...\n"
    )
    tree = ast.parse(source)
    prefixes = _router_prefixes(tree)
    assert prefixes == {"router": "/api/chat", "patient_router": "/api/patients"}
