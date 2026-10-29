# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from app.services.transcription_profile import (
    register_medical_profile_upgrade_policy,
    reset_medical_profile_upgrade_policy,
    transcription_profile_for_user,
)


@pytest.fixture(autouse=True)
def _reset_policy() -> None:
    reset_medical_profile_upgrade_policy()


@pytest.mark.parametrize("provider_type", ["therapist", "prescriber", "both"])
def test_every_provider_type_defaults_to_standard_profile(provider_type: str) -> None:
    db = MagicMock()
    user = SimpleNamespace(provider_type=provider_type)
    db.get.return_value = user
    policy = MagicMock(return_value=False)
    register_medical_profile_upgrade_policy(policy)

    assert transcription_profile_for_user(db, "user-1") == "standard"
    policy.assert_called_once_with(db, user)


def test_deployment_policy_can_upgrade_a_clinician() -> None:
    db = MagicMock()
    user = SimpleNamespace(provider_type="therapist")
    db.get.return_value = user
    register_medical_profile_upgrade_policy(lambda session, row: session is db and row is user)

    assert transcription_profile_for_user(db, "user-1") == "medical"


def test_missing_user_fails_to_standard_profile() -> None:
    db = MagicMock()
    db.get.return_value = None

    assert transcription_profile_for_user(db, "missing") == "standard"
