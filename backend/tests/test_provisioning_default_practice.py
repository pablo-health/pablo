# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Boot must not invent a practice underneath a deployment that has its own.

An unregistered practice schema is invisible to every per-tenant migration —
they iterate ``platform.practices`` — so it never receives one again. It then
drifts until something notices, and the thing that notices is this same boot
path: ``create_practice_schema`` re-runs the RLS guard over the accumulated
staleness and refuses to start.

That is not hypothetical. On 2026-09-09 a retired table (``booking_policy``)
was dropped from every registered tenant and survived in exactly one
unregistered schema, which took the deployment down at boot — the guard was
right, and the schema should never have existed.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.db import provisioning


class _Recorder:
    """Captures which schemas boot decided to provision."""

    def __init__(self) -> None:
        self.created: list[str] = []

    def __call__(self, _engine: Any, schema_name: str) -> None:
        self.created.append(schema_name)


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(provisioning, "create_practice_schema", recorder)
    return recorder


class TestTheDeploymentsOwnPractice:
    def test_a_first_boot_provisions_one(
        self, created: _Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty database gets its practice with no operator step.

        This is the property that makes the check safe to add: a self-hoster
        must not have to discover a manual step by hitting a refusal.
        """
        monkeypatch.setattr(provisioning, "_has_any_practice", lambda _engine: False)

        provisioning._provision_core_schemas(object())  # type: ignore[attr-defined]

        assert provisioning.DEFAULT_PRACTICE_OWN_SCHEMA in created.created

    def test_a_deployment_with_practices_gets_no_extra_one(
        self, created: _Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The orphan case. A deployment that provisions its own tenants must
        not have a second, unregistered practice invented underneath it."""
        monkeypatch.setattr(provisioning, "_has_any_practice", lambda _engine: True)

        provisioning._provision_core_schemas(object())  # type: ignore[attr-defined]

        assert provisioning.DEFAULT_PRACTICE_OWN_SCHEMA not in created.created, (
            "boot provisioned a practice schema on a deployment that already has "
            "practices — it will be in the database and in nobody's registry, so "
            "every per-tenant migration will skip it"
        )

    def test_the_template_is_built_either_way(
        self, created: _Recorder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The template is not a practice; every deployment needs its shape."""
        for has_practices in (False, True):
            created.created.clear()
            monkeypatch.setattr(
                provisioning, "_has_any_practice", lambda _engine, v=has_practices: v
            )

            provisioning._provision_core_schemas(object())  # type: ignore[attr-defined]

            assert provisioning.DEFAULT_PRACTICE_SCHEMA in created.created, (
                f"template not built when has_practices={has_practices}"
            )
