# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Settings."""

from __future__ import annotations

import pytest
from app.settings import Settings


def _make(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        **overrides,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "project_id",
    [
        "pablohealth-prod",
        "pablohealth-production",
        "pablohealth-prod1",
        "pablohealth-prod2",
    ],
)
def test_is_prod_project_matches_prod_variants(project_id: str) -> None:
    assert _make(environment="staging", gcp_project_id=project_id).is_prod_project


@pytest.mark.parametrize(
    "project_id",
    [
        "pablohealth-dev",
        "pablohealth-staging",
        "pablohealth-pentest",
        "pablohealth-test",
        "pablohealth-reproduction",
        "pablohealth-approved",
        "pablohealth-product-dev",
        "pablohealth-prod-us",
        "",
    ],
)
def test_is_prod_project_rejects_non_prod_variants(project_id: str) -> None:
    assert not _make(environment="staging", gcp_project_id=project_id).is_prod_project


def test_is_prod_project_honors_environment_override() -> None:
    assert _make(environment="production", gcp_project_id="legacy-cluster-a").is_prod_project


def test_is_prod_project_default_is_false() -> None:
    assert not _make(environment="development").is_prod_project


def test_internal_actor_user_ids_default_is_empty() -> None:
    assert _make().internal_actor_user_ids == set()


def test_internal_actor_user_ids_parses_and_strips() -> None:
    settings = _make(INTERNAL_ACTOR_USER_IDS=" bot-1 , bot-2 ,, ")
    assert settings.internal_actor_user_ids == {"bot-1", "bot-2"}
