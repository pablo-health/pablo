# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rule 5 of ``backend/scripts/check_route_audit.py``: no underscore-named
``Depends`` / ``Security`` parameter under ``app/``, with a baseline that only
shrinks. Fixture modules are written to a temporary ``app/`` tree so each case
is checked in isolation from the real codebase."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_route_audit.py"
_spec = importlib.util.spec_from_file_location("check_route_audit_rule5", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guardrail)

_HEADER = """\
from typing import Annotated

from fastapi import APIRouter, Depends, Security

router = APIRouter()


def f() -> None:
    return None
"""


def _violations(tmp_path: Path, body: str, baseline: set[str] | None = None) -> list[str]:
    root = tmp_path / "app"
    root.mkdir()
    module = root / "mod.py"
    module.write_text(_HEADER + body)
    violations: list[str] = guardrail.underscore_depends_violations(
        [module], root=root, baseline=baseline or set(), full_scan=True
    )
    return violations


@pytest.mark.parametrize(
    ("signature", "param"),
    [
        ("_x: None = Depends(f)", "_x"),
        ("_x: Annotated[None, Depends(f)]", "_x"),
        ("_x: None = Security(f)", "_x"),
        ("_: Annotated[None, Security(f)]", "_"),
        ("*, _ctx: None = Depends(f)", "_ctx"),
    ],
)
def test_underscore_dependency_param_fails(tmp_path: Path, signature: str, param: str) -> None:
    body = f'\n\n@router.get("/x")\ndef handler({signature}) -> None:\n    return None\n'
    violations = _violations(tmp_path, body)
    assert len(violations) == 1
    assert violations[0].startswith("app/mod.py:")
    assert f"handler declares dependency parameter `{param}`" in violations[0]
    assert "dependencies=[Depends(...)]" in violations[0]


def test_underscore_param_in_a_plain_dependency_provider_fails(tmp_path: Path) -> None:
    body = "\n\ndef get_repo(_ctx: None = Depends(f)) -> int:\n    return 1\n"
    assert len(_violations(tmp_path, body)) == 1


def test_moved_form_passes(tmp_path: Path) -> None:
    body = (
        '\n\n@router.get("/x", dependencies=[Depends(f), Security(f)])\n'
        "def handler(ctx: Annotated[None, Depends(f)], _unused: int = 0) -> None:\n"
        "    return ctx\n"
    )
    assert _violations(tmp_path, body) == []


def test_router_level_dependencies_pass(tmp_path: Path) -> None:
    body = (
        "\n\nrouter2 = APIRouter(dependencies=[Depends(f)])\n\n\n"
        '@router2.get("/x")\ndef handler() -> None:\n    return None\n'
    )
    assert _violations(tmp_path, body) == []


def test_baselined_site_passes(tmp_path: Path) -> None:
    body = '\n\n@router.get("/x")\ndef handler(_x: None = Depends(f)) -> None:\n    return None\n'
    assert _violations(tmp_path, body, baseline={"app/mod.py:handler:_x"}) == []


def test_new_param_on_a_baselined_function_still_fails(tmp_path: Path) -> None:
    body = (
        '\n\n@router.get("/x")\n'
        "def handler(_x: None = Depends(f), _y: None = Depends(f)) -> None:\n"
        "    return None\n"
    )
    violations = _violations(tmp_path, body, baseline={"app/mod.py:handler:_x"})
    assert len(violations) == 1
    assert "`_y`" in violations[0]


def test_baseline_line_for_a_site_that_no_longer_exists_fails(tmp_path: Path) -> None:
    body = (
        '\n\n@router.get("/x", dependencies=[Depends(f)])\n'
        "def handler() -> None:\n    return None\n"
    )
    violations = _violations(tmp_path, body, baseline={"app/mod.py:handler:_x"})
    assert len(violations) == 1
    assert "lists `app/mod.py:handler:_x`, which no longer exists" in violations[0]


def test_partial_scan_skips_the_stale_baseline_check(tmp_path: Path) -> None:
    """A hook passes one edited file; the rest of the baseline is not stale."""
    root = tmp_path / "app"
    root.mkdir()
    module = root / "mod.py"
    module.write_text(_HEADER)
    violations = guardrail.underscore_depends_violations(
        [module], root=root, baseline={"app/other.py:handler:_x"}, full_scan=False
    )
    assert violations == []


def test_baseline_file_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("# header\n\napp/mod.py:handler:_x\n")
    assert guardrail.load_underscore_depends_baseline(baseline) == {"app/mod.py:handler:_x"}


def test_committed_baseline_matches_the_tree_exactly() -> None:
    """The real tree: no new site, and no baseline line left behind by a sweep."""
    assert guardrail.underscore_depends_violations(guardrail._app_files(None)) == []
