#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Find logger / print / structlog calls that reference PHI field names.

Enforces CLAUDE.md guardrail #5. AST-walks every ``.py`` file under
``backend/app/``. For every call expression whose callee matches a
known logging shape (``logger.info``, ``print``, ``structlog.*``, …),
we pull the string-ish argument literals and match against a list of
PHI token names. Calls on ``audit`` / ``self._audit`` are intentional
and excluded.

Output is ``file:line  snippet  suggested fix`` per finding.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_APP = REPO_ROOT / "backend" / "app"

LEVELS: frozenset[str] = frozenset(
    {"debug", "info", "warning", "warn", "error", "critical", "exception"}
)
LOGGER_NAMES: frozenset[str] = frozenset({"logger", "log", "logging"})

PHI_TOKENS: tuple[str, ...] = (
    "patient_name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "dob",
    "diagnosis",
    "ssn",
    "mrn",
    "transcript",
    "note_body",
    "address",
)
PHI_RE = re.compile(r"\b(" + "|".join(PHI_TOKENS) + r")\b")

# Files where PHI references in log-ish calls are sanctioned: the audit
# service itself, plus the tests folder.
EXCLUDED_SUFFIXES: tuple[str, ...] = (
    "services/audit_service.py",
    "services/audit.py",
)


def _is_excluded(path: Path) -> bool:
    rel = str(path.relative_to(REPO_ROOT))
    if "/tests/" in rel or rel.endswith("_test.py"):
        return True
    return any(rel.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _callee_name(call: ast.Call) -> str | None:
    """Return a dotted callee name, e.g. 'logger.info' or 'audit.log_event'."""
    func = call.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return None


def _is_audit_call(callee: str) -> bool:
    head = callee.split(".", 1)[0]
    # audit.log_*(...), self._audit.log_*(...), self.audit_service.log_*(...)
    return head in {"audit", "_audit"} or ".audit." in f".{callee}." or ".audit_service." in f".{callee}."


def _is_log_call(callee: str) -> bool:
    parts = callee.split(".")
    if len(parts) == 1:
        return parts[0] == "print"
    root = parts[0]
    tail = parts[-1]
    if root == "structlog":
        return True
    if root in LOGGER_NAMES:
        return tail in LEVELS or tail.startswith("log")
    # self.logger.info, self._logger.info
    if tail in LEVELS and any(p in LOGGER_NAMES or p.lstrip("_") in LOGGER_NAMES for p in parts):
        return True
    return False


def _extract_text(node: ast.AST) -> str:
    """Return any text we can scan for PHI tokens — strings, f-strings, formats."""
    chunks: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            chunks.append(sub.value)
        elif isinstance(sub, ast.FormattedValue):
            try:
                chunks.append(ast.unparse(sub.value))
            except Exception:
                pass
        elif isinstance(sub, ast.keyword) and sub.arg:
            chunks.append(sub.arg)
    return " | ".join(chunks)


def _snippet(source: str, lineno: int) -> str:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:120]
    return ""


def _find_phi_tokens(text: str) -> list[str]:
    return sorted(set(PHI_RE.findall(text)))


def main() -> int:
    if not BACKEND_APP.is_dir():
        print(f"backend app dir not found: {BACKEND_APP}", file=sys.stderr)
        return 2

    findings: list[dict[str, str]] = []

    for py_file in sorted(BACKEND_APP.rglob("*.py")):
        if _is_excluded(py_file):
            continue
        source = py_file.read_text(errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = _callee_name(node)
            if callee is None:
                continue
            if _is_audit_call(callee):
                continue
            if not _is_log_call(callee):
                continue

            text_blob = " ".join(_extract_text(arg) for arg in node.args)
            text_blob += " " + " ".join(_extract_text(kw.value) for kw in node.keywords)
            tokens = _find_phi_tokens(text_blob)
            if not tokens:
                continue

            rel = str(py_file.relative_to(REPO_ROOT))
            findings.append(
                {
                    "loc": f"{rel}:{node.lineno}",
                    "callee": callee,
                    "tokens": ", ".join(tokens),
                    "snippet": _snippet(source, node.lineno),
                }
            )

    if not findings:
        print("phi-log-scan: clean - no logger/print/structlog calls reference PHI fields.")
        return 0

    print("# PHI Log Leaks\n")
    print(f"Found {len(findings)} call(s) that may write PHI to stdout.\n")
    for f in findings:
        print(f"- `{f['loc']}`  `{f['callee']}(...)`  PHI: {f['tokens']}")
        print(f"    {f['snippet']}")
        print(
            "    fix: drop the PHI token; log a stable id instead "
            "(`patient_id`, `session_id`, `actor_id`) "
            "or route to `AuditService` if the record is intentional."
        )
        print()

    print("See CLAUDE.md guardrail #5.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
