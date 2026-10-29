# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Guardrail test: a tenant Alembic revision must not touch ``platform.``.

Enforces CLAUDE.md guardrail #4. The check lives in
``backend/scripts/check_tenant_chain_platform.py`` (pure stdlib) so the pre-commit
hook and this CI gate share one implementation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_tenant_chain_platform.py"
_spec = importlib.util.spec_from_file_location("check_tenant_chain_platform", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
guardrail = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guardrail)

_HEADER = 'revision: str = "zzzzzzzzzzzz"\n\n\ndef upgrade() -> None:\n'


def _violations(tmp_path: Path, body: str) -> list[str]:
    revision = tmp_path / "zzzzzzzzzzzz_probe.py"
    revision.write_text(_HEADER + body, encoding="utf-8")
    return guardrail.find_violations([revision])


def test_no_new_tenant_revision_touches_platform() -> None:
    violations = guardrail.find_violations()
    assert not violations, (
        "A tenant-chain revision touches platform. CLAUDE.md guardrail #4: the tenant chain "
        "re-runs once per practice schema, after the platform chain, and can undo platform "
        "work. Put platform DDL in backend/alembic_platform/ instead.\n\n" + "\n".join(violations)
    )


def test_legacy_allowlist_only_names_revisions_that_still_touch_platform() -> None:
    still_touching = set(guardrail.platform_revisions())
    stale = guardrail.LEGACY_PLATFORM_REVISIONS - still_touching
    assert not stale, f"Remove from LEGACY_PLATFORM_REVISIONS (it may only shrink): {sorted(stale)}"


@pytest.mark.parametrize(
    "body",
    [
        '    op.execute("CREATE INDEX x ON platform.users (id)")\n',
        '    op.execute("ALTER TABLE platform.users ADD COLUMN x int")\n',
        '    op.add_column("users", sa.Column("x", sa.Integer()), schema="platform")\n',
        '    op.create_index("ix", "users", ["id"], schema="platform")\n',
    ],
)
def test_platform_ddl_is_refused(tmp_path: Path, body: str) -> None:
    assert _violations(tmp_path, body)


@pytest.mark.parametrize(
    "body",
    [
        '    """Mentions platform.users in prose."""\n    pass\n',
        '    op.create_table("t", sa.Column("u", sa.ForeignKey("platform.users.id")))\n',
        '    op.create_table("t", sa.ForeignKeyConstraint(["u"], ["platform.users.id"]))\n',
        '    op.execute("CREATE TABLE t (u uuid REFERENCES platform.users(id))")\n',
    ],
)
def test_foreign_keys_into_platform_are_allowed(tmp_path: Path, body: str) -> None:
    assert not _violations(tmp_path, body)
