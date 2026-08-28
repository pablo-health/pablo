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


def test_is_prod_project_keys_on_project_not_environment_label() -> None:
    """A non-prod project may run ENVIRONMENT=production on purpose (so every
    code path behaves as it will in production). That label must NOT make the
    project count as prod — this property only gates the reserved
    test-identity bypasses, which scope by which project holds real data."""
    assert not _make(environment="production", gcp_project_id="pablohealth-dev").is_prod_project


def test_is_prod_project_environment_fallback_without_project_id() -> None:
    """Self-hosted off GCP (no project id): the environment string is the only
    signal left, and "production" must still mean no test bypasses."""
    assert _make(environment="production", gcp_project_id="").is_prod_project
    assert not _make(environment="development", gcp_project_id="").is_prod_project


def test_is_prod_project_default_is_false() -> None:
    assert not _make(environment="development").is_prod_project


def test_internal_actor_user_ids_default_is_empty() -> None:
    assert _make().internal_actor_user_ids == set()


def test_internal_actor_user_ids_parses_and_strips() -> None:
    settings = _make(INTERNAL_ACTOR_USER_IDS=" bot-1 , bot-2 ,, ")
    assert settings.internal_actor_user_ids == {"bot-1", "bot-2"}


def test_assemblyai_speaker_labels_channels_default_is_empty() -> None:
    assert _make().assemblyai_speaker_labels_channels == []


def test_assemblyai_speaker_labels_channels_parses_and_strips() -> None:
    settings = _make(assemblyai_speaker_labels_channels=" Client , , ")
    assert settings.assemblyai_speaker_labels_channels == ["Client"]
