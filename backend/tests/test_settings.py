# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Settings helpers used by security gates."""

from __future__ import annotations

import pytest
from app.settings import Settings


def _make(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        **overrides,  # type: ignore[arg-type]
    )


# ── is_prod_project ────────────────────────────────────────────────────────
#
# is_prod_project gates security bypasses (test-identity allowlist skip,
# MFA skip for e2e accounts). It must:
#   - match real prod project naming variants, not just `<x>-prod`
#   - reject substring false positives like `reproduction`
#   - also return True when `environment` is set to "production" as a
#     belt-and-suspenders signal (env-var-misconfiguration safety)


@pytest.mark.parametrize(
    "project_id",
    [
        "pablohealth-prod",
        "pablohealth-production",
        "pablohealth-prod1",
        "pablohealth-prod2",
        "pablohealth-prod-us",  # no — see negative cases; this is here as a doc
    ][:-1],
)
def test_is_prod_project_matches_prod_variants(project_id: str) -> None:
    s = _make(environment="staging", gcp_project_id=project_id)
    assert s.is_prod_project is True, f"{project_id!r} should match as prod"


@pytest.mark.parametrize(
    "project_id",
    [
        "pablohealth-dev",
        "pablohealth-staging",
        "pablohealth-pentest",
        "pablohealth-test",
        "pablohealth-reproduction",  # substring trap
        "pablohealth-approved",  # substring trap
        "pablohealth-product-dev",  # "prod" appears mid-name
        "pablohealth-prod-us",  # suffix-after-prod is not a prod project
        "",
    ],
)
def test_is_prod_project_rejects_non_prod_variants(project_id: str) -> None:
    s = _make(environment="staging", gcp_project_id=project_id)
    assert s.is_prod_project is False, f"{project_id!r} should not match as prod"


def test_is_prod_project_respects_environment_override() -> None:
    """Belt-and-suspenders: explicit environment=production wins even if
    the project ID doesn't match the naming pattern."""
    s = _make(environment="production", gcp_project_id="legacy-cluster-a")
    assert s.is_prod_project is True


def test_is_prod_project_default_is_false() -> None:
    """Empty project id + non-prod environment → safe default."""
    s = _make(environment="development")
    assert s.is_prod_project is False
