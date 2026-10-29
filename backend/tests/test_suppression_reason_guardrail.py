# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""CI gate for CLAUDE.md guardrail #7: every type-suppression carries a reason.

The scan lives in ``scripts/check_suppression_reasons.py`` so the pre-commit
hook and this test run the same code and cannot drift.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_suppression_reasons.py"
_spec = importlib.util.spec_from_file_location("check_suppression_reasons", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guardrail)


def _violations(tmp_path: Path, name: str, source: str) -> list[int]:
    path = tmp_path / name
    path.write_text(source)
    check = guardrail.python_violations if name.endswith(".py") else guardrail.typescript_violations
    return check(path)


def test_production_code_has_no_unreasoned_suppressions() -> None:
    violations = guardrail.find_violations()
    assert not violations, (
        "Type suppressions need a one-line reason (CLAUDE.md guardrail #7): fix the type, "
        "or add a trailing comment or a comment on the line above.\n\n" + "\n".join(violations)
    )


def test_python_ignore_without_reason_is_flagged(tmp_path: Path) -> None:
    assert _violations(tmp_path, "m.py", "x = f()  # type: ignore[arg-type]\n") == [1]


def test_python_noqa_is_not_a_reason(tmp_path: Path) -> None:
    source = "import x  # type: ignore[import-not-found]  # noqa: PLC0415\n"
    assert _violations(tmp_path, "m.py", source) == [1]


def test_python_trailing_or_preceding_comment_is_a_reason(tmp_path: Path) -> None:
    source = (
        "a = f()  # type: ignore[arg-type]  # stub is wrong\n"
        "# the SDK returns Any\n"
        "b = g()  # type: ignore[no-any-return]\n"
        "c = h()  # type: ignore[no-any-return]\n"
    )
    assert _violations(tmp_path, "m.py", source) == []


def test_typescript_escape_without_reason_is_flagged(tmp_path: Path) -> None:
    source = "const a = b as unknown as T\nconst c = d as any\n// @ts-ignore\nx()\n"
    assert _violations(tmp_path, "m.ts", source) == [1, 2, 3]


def test_typescript_trailing_or_preceding_comment_is_a_reason(tmp_path: Path) -> None:
    source = (
        "const a = b as unknown as T // server validated the shape\n"
        "// the library types this as unknown\n"
        "const c = d as any\n"
        "// @ts-expect-error -- happy-dom provides none\n"
        "x()\n"
    )
    assert _violations(tmp_path, "m.ts", source) == []
