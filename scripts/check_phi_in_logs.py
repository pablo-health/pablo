#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pre-commit / CI guardrail: forbid logger.*() callsites that pass PHI-keyed
arguments.

Pattern blocked (positional or kwarg dict):

    logger.info("note saved for %s", patient_id)        # positional
    logger.info("note saved", extra={"patient_id": pid}) # extra-dict kwarg
    logger.info("note saved", patient_id=pid)            # bare kwarg

These all flow into the LogRecord and would only be redacted at run time by
the RedactPHIFilter. The defense in depth is that they should never be
written in the first place — log the operation, not the data.

This script is the static gate. See backend/app/logging_config.py for the
runtime gate (which catches anything that slips past).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"

PHI_KEYS: frozenset[str] = frozenset(
    {
        "patient_id",
        "patient_name",
        "patient_email",
        "patient_phone",
        "patient_dob",
        "dob",
        "ssn",
        "soap_text",
        "transcript",
        "audio_path",
        "note_content",
        "prompt_text",
        "chat_message_content",
        "chat_content",
        "message_content",
    }
)

LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}

# Files this guardrail intentionally does not police:
#  - the logging_config / test files reference PHI keys by name on purpose
SKIP_FILES = {
    BACKEND / "app" / "logging_config.py",
    BACKEND / "tests" / "test_logging_config.py",
}


def _is_logger_call(call: ast.Call) -> bool:
    """Return True if `call` looks like `<something>.<log-method>(...)`."""
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr not in LOG_METHODS:
        return False
    # Heuristic: receiver name contains "log" (logger / logger_name / _log / etc.)
    # — avoids false positives on e.g. session.info() or http.error().
    receiver = call.func.value
    if isinstance(receiver, ast.Name):
        return "log" in receiver.id.lower()
    if isinstance(receiver, ast.Attribute):
        return "log" in receiver.attr.lower()
    return False


def _violations_in_call(call: ast.Call) -> list[str]:
    violations: list[str] = []

    # logger.info("msg", patient_id=x) — direct PHI-keyed kwarg
    for kw in call.keywords:
        if kw.arg is None:  # **kwargs splat — can't inspect statically
            continue
        if kw.arg in PHI_KEYS:
            violations.append(f"PHI-keyed kwarg `{kw.arg}=`")
        if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
            for key in kw.value.keys:
                if isinstance(key, ast.Constant) and key.value in PHI_KEYS:
                    violations.append(f"PHI key in extra={{...}}: `{key.value}`")

    # logger.info("note for patient %s", patient_id) — positional name match
    for arg in call.args[1:]:  # skip the message string
        if isinstance(arg, ast.Name) and arg.id in PHI_KEYS:
            violations.append(f"PHI-named positional arg `{arg.id}`")
        if isinstance(arg, ast.Attribute) and arg.attr in PHI_KEYS:
            violations.append(f"PHI-named attribute access `.{arg.attr}`")

    return violations


def _scan_file(path: Path) -> list[tuple[int, str]]:
    if path in SKIP_FILES:
        return []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    findings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_logger_call(node):
            continue
        for msg in _violations_in_call(node):
            findings.append((node.lineno, msg))
    return findings


def main(argv: list[str]) -> int:
    if not BACKEND.exists():
        print(f"[phi-log-check] backend dir not found at {BACKEND}", file=sys.stderr)
        return 0

    targets = [p for p in BACKEND.rglob("*.py") if "/tests_integration/" not in str(p)]
    failed = False
    for path in targets:
        for lineno, msg in _scan_file(path):
            rel = path.relative_to(REPO_ROOT)
            print(f"::error file={rel},line={lineno}::PHI in log call — {msg}")
            failed = True

    if failed:
        print(
            "\n[phi-log-check] Forbidden PHI-keyed args found in logger.* calls.\n"
            "Log the operation, not the data. Use opaque ids (user_id, request_id)\n"
            "or audit_service.log() for PHI-adjacent events.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
