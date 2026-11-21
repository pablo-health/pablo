# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail #3: no raw SQL in route handlers (``backend/scripts/check_route_raw_sql.py``).

Fixture modules are written to a temporary ``app/routes/`` tree so each case is
checked in isolation from the real codebase; the last test holds the real tree
to the committed baseline."""

from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_route_raw_sql.py"
_spec = importlib.util.spec_from_file_location("check_route_raw_sql", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guardrail)


def _violations(tmp_path: Path, body: str, baseline: Counter[str] | None = None) -> list[str]:
    root = tmp_path / "app" / "routes"
    root.mkdir(parents=True)
    (root / "mod.py").write_text(body)
    violations: list[str] = guardrail.raw_sql_violations(
        guardrail.route_files(root), root=root, baseline=baseline or Counter()
    )
    return violations


@pytest.mark.parametrize(
    "call",
    [
        "session.execute(select(Row).where(Row.id == 1))",
        "db.execute(update(Row).values(x=1))",
        "anything.execute(sa.delete(Row))",
        "self.session.scalars(stmt)",
        "conn.scalar(text('SELECT 1'))",
        "other.execute(insert(Row).values(x=1).returning(Row.id))",
    ],
)
def test_raw_query_in_a_route_fails(tmp_path: Path, call: str) -> None:
    violations = _violations(tmp_path, f"def handler():\n    return {call}\n")
    assert len(violations) == 1
    assert violations[0].startswith("app/routes/mod.py:2 handler runs a query")


@pytest.mark.parametrize(
    "call",
    [
        "scheduler.execute(user.id, on_request=True)",
        "repo.list_for(user.id)",
        "session.execute",
        "result.scalars().first()",
    ],
)
def test_calls_that_are_not_queries_pass(tmp_path: Path, call: str) -> None:
    assert _violations(tmp_path, f"def handler():\n    return {call}\n") == []


def test_baselined_site_passes_and_a_second_one_in_the_same_function_fails(
    tmp_path: Path,
) -> None:
    one = "def handler():\n    session.execute(select(Row))\n"
    two = one + "    session.execute(select(Other))\n"
    baseline = Counter({"app/routes/mod.py:handler": 1})
    assert _violations(tmp_path / "a", one, baseline) == []
    violations = _violations(tmp_path / "b", two, baseline)
    assert len(violations) == 1
    assert violations[0].startswith("app/routes/mod.py:3 handler")


def test_stale_baseline_line_fails(tmp_path: Path) -> None:
    baseline = Counter({"app/routes/mod.py:handler": 1})
    violations = _violations(tmp_path, "def handler():\n    return None\n", baseline)
    assert violations == [
        "route_raw_sql_baseline.txt lists `app/routes/mod.py:handler` once more than "
        "there are raw queries in it: delete the line (the baseline only shrinks)"
    ]


def test_nested_function_is_named_by_its_full_scope(tmp_path: Path) -> None:
    body = "def outer():\n    def inner():\n        db.execute(select(Row))\n"
    violations = _violations(tmp_path, body)
    assert violations[0].startswith("app/routes/mod.py:3 outer.inner")


def test_baseline_file_counts_repeated_lines_and_ignores_comments(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# header\n\napp/routes/mod.py:handler\napp/routes/mod.py:handler\n")
    assert guardrail.load_baseline(baseline) == Counter({"app/routes/mod.py:handler": 2})


def test_committed_baseline_matches_the_tree_exactly() -> None:
    """The real tree: no new raw query, and no baseline line left behind by a move."""
    assert guardrail.raw_sql_violations(guardrail.route_files()) == []
