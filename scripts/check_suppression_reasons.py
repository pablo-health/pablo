#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Gate for CLAUDE.md guardrail #7: every type-system escape hatch says why.

Flags, in production code (tests are excluded), any of

* Python ``# type: ignore``
* TypeScript ``as unknown as X``, ``as any``, ``@ts-ignore``, ``@ts-expect-error``

that carries no reason. A reason is either a trailing comment on the same line
(``# type: ignore[arg-type]  # lib stub is wrong``) or an explanatory comment
on the line directly above. A run of consecutive suppressed lines shares the
comment above the first one. ``# noqa`` is a different tool's marker, not a
reason.

Exit 0 when clean, 1 with one ``path:line`` per violation otherwise. Shared by
the pre-commit hook and ``backend/tests/test_suppression_reason_guardrail.py``.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PYTHON_ROOTS = ("backend/app", "backend/bin", "backend/scripts", "backend/entrypoint.py")
TYPESCRIPT_ROOTS = ("frontend/src", "frontend/app")

SKIPPED_DIR_PARTS = frozenset(
    {"node_modules", ".next", "dist", "build", "__pycache__", ".venv", "venv"}
    | {"test", "tests", "__tests__", "e2e", "__mocks__"}
)
TEST_FILE_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

PY_IGNORE = re.compile(r"#\s*type:\s*ignore(?:\[[^\]]*\])?(?P<rest>.*)$")
PY_TRAILING_REASON = re.compile(r"#\s*(?!noqa\b)\S")
TS_ESCAPE = re.compile(r"\bas\s+unknown\s+as\b|\bas\s+any\b|@ts-ignore|@ts-expect-error")
TS_TRAILING_REASON = re.compile(r"(?://|/\*)\s*\S")
TS_DIRECTIVE_REASON = re.compile(r"@ts-(?:ignore|expect-error)\s*[-:]*\s*\S")


def _source_files(roots: Sequence[str], suffixes: tuple[str, ...]) -> Iterable[Path]:
    for root in roots:
        base = REPO_ROOT / root
        candidates = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
        for path in candidates:
            relative_parts = path.relative_to(REPO_ROOT).parts
            if (
                path.is_file()
                and path.suffix in suffixes
                and not path.name.endswith(TEST_FILE_SUFFIXES)
                and not SKIPPED_DIR_PARTS.intersection(relative_parts)
            ):
                yield path


def _commented_above(lines: list[str], index: int, marker: str, comment_prefix: str) -> bool:
    """True when the run of marked lines ending at ``index`` has a comment above it."""
    above = index - 1
    while above >= 0 and marker in lines[above]:
        above -= 1
    return above >= 0 and lines[above].lstrip().startswith(comment_prefix)


def python_violations(path: Path) -> list[int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [
        number
        for number, line in enumerate(lines, 1)
        if (match := PY_IGNORE.search(line))
        and not PY_TRAILING_REASON.search(match["rest"])
        and not _commented_above(lines, number - 1, "type: ignore", "#")
    ]


def _typescript_has_reason(lines: list[str], index: int) -> bool:
    line = lines[index]
    match = TS_ESCAPE.search(line)
    assert match is not None
    if TS_DIRECTIVE_REASON.search(line) or TS_TRAILING_REASON.search(line[match.end() :]):
        return True
    return _commented_above(lines, index, match.group(0), "//")


def typescript_violations(path: Path) -> list[int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [
        number
        for number, line in enumerate(lines, 1)
        if TS_ESCAPE.search(line)
        and not line.lstrip().startswith(("*", "/*"))  # prose in a block comment
        and not _typescript_has_reason(lines, number - 1)
    ]


def find_violations() -> list[str]:
    found: list[str] = []
    for path in _source_files(PYTHON_ROOTS, (".py",)):
        found += [f"{path.relative_to(REPO_ROOT)}:{n}" for n in python_violations(path)]
    for path in _source_files(TYPESCRIPT_ROOTS, (".ts", ".tsx")):
        found += [f"{path.relative_to(REPO_ROOT)}:{n}" for n in typescript_violations(path)]
    return found


def main() -> int:
    violations = find_violations()
    if not violations:
        return 0
    print(
        "Type-suppression without a reason (CLAUDE.md guardrail #7). Fix the type, or add a\n"
        "one-line reason: a trailing comment, or a comment on the line directly above.\n",
        file=sys.stderr,
    )
    print("\n".join(violations), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
