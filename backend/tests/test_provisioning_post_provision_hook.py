# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the post-provision hook registry.

Hook *dispatch* through ``create_practice_schema`` requires a real
Postgres (covered separately by a downstream integration test in
the deployment overlay once it registers its callback). These tests
just exercise the registration / reset surface so OSS callers can rely
on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from app.db import provisioning

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_FAKE_ENGINE = cast("Engine", object())


@pytest.fixture(autouse=True)
def _reset_hooks():
    provisioning.reset_post_provision_hooks()
    yield
    provisioning.reset_post_provision_hooks()


def test_register_and_run_invokes_hook_in_order() -> None:
    calls: list[tuple[object, str]] = []

    provisioning.register_post_provision_hook(lambda _engine, schema: calls.append(("a", schema)))
    provisioning.register_post_provision_hook(lambda _engine, schema: calls.append(("b", schema)))

    provisioning._run_post_provision_hooks(_FAKE_ENGINE, "practice_test")

    assert calls == [("a", "practice_test"), ("b", "practice_test")]


def test_run_with_no_registered_hooks_is_a_noop() -> None:
    # Self-hosted OSS deployments never load the SaaS overlay, so the
    # hook list stays empty. The fresh-template branch in
    # create_practice_schema must not crash when it dispatches.
    provisioning._run_post_provision_hooks(_FAKE_ENGINE, "practice_test")


def test_reset_clears_registered_hooks() -> None:
    calls: list[str] = []
    provisioning.register_post_provision_hook(lambda _engine, schema: calls.append(schema))
    provisioning.reset_post_provision_hooks()
    provisioning._run_post_provision_hooks(_FAKE_ENGINE, "practice_test")
    assert calls == []
