# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A module inside ``app`` may not import ``app`` by absolute name.

The package is importable under two names in this repository: as ``app`` from
``backend/``, which is how the test suite and every local tool load it, and as
``backend.app`` from the repository root, which is how the container runs it.
An absolute ``from app.x import y`` inside the package resolves under the first
and raises ``ModuleNotFoundError`` under the second.

Nothing else catches that. Ruff does not, mypy does not, and the whole unit
suite passes, because they all run with ``backend/`` on the path. The first
thing to notice is the container failing its health check at boot, which in
this repository means the end-to-end job dying at "start the stack" with a
traceback nobody reads until they go looking — which is exactly how this test
came to exist.

Relative imports (``from ..db.models import ...``) work under both names,
which is why they are the rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"


def _imports_app_absolutely(path: Path) -> bool:
    """Does this module actually import ``app`` by absolute name?

    Parsed rather than pattern-matched: half the apparent hits in this package
    are usage examples inside docstrings, and a guard that cannot tell prose
    from code is a guard people learn to route around.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        # On ImportFrom, `level` is 0 for an absolute import and >0 for a
        # relative one, which is the whole distinction being policed.
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and (node.module or "").split(".")[0] == "app"
        ):
            return True
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "app" for alias in node.names
        ):
            return True
    return False


#: Modules that already did this before the rule was written down. They are
#: not reachable from ``main.py`` at import time, so the container never loads
#: them and they have never broken a boot — which is why they are tolerated
#: rather than fixed under cover of an unrelated change.
#:
#: This list may only SHRINK. Adding to it means shipping a module that cannot
#: be imported by the name the container uses, and the next thing that imports
#: it from ``main`` takes the whole service down at startup.
_GRANDFATHERED: frozenset[str] = frozenset(
    {
        "jobs/pentest_context.py",
        "jobs/pentest_explore.py",
        "jobs/pentest_runner.py",
        "services/signals/__init__.py",
        "services/signals/entity_consistency.py",
        "services/signals/hedging.py",
        "services/signals/negation.py",
        "services/signals/temporal.py",
    }
)


def test_no_module_inside_app_imports_app_absolutely() -> None:
    offenders = sorted(
        str(path.relative_to(_APP))
        for path in _APP.rglob("*.py")
        if _imports_app_absolutely(path) and str(path.relative_to(_APP)) not in _GRANDFATHERED
    )

    assert not offenders, (
        "These modules import `app` by absolute name, which fails under the "
        f"`backend.app` name the container runs as: {offenders}. "
        "Use a relative import instead."
    )


def test_the_grandfathered_list_has_not_gone_stale() -> None:
    """A waiver left behind after its module was fixed hides the next one."""
    stale = sorted(
        name
        for name in _GRANDFATHERED
        if not (_APP / name).exists() or not _imports_app_absolutely(_APP / name)
    )

    assert not stale, f"No longer needed in the grandfathered list: {stale}"
