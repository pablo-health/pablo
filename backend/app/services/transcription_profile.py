# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Choose the transcription quality profile for a clinician.

The engine defaults every clinician to the standard profile. A deployment may
register an upgrade policy for clinicians it chooses to place on a medical
speech model; clinicians cannot select the provider or model themselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ..db.platform_models import PlatformUserRow

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

type TranscriptionProfile = Literal["standard", "medical"]
type MedicalProfileUpgradePolicy = Callable[["Session", PlatformUserRow], bool]

_medical_profile_upgrade_policy: MedicalProfileUpgradePolicy | None = None


def register_medical_profile_upgrade_policy(policy: MedicalProfileUpgradePolicy) -> None:
    """Register a deployment policy that may upgrade non-prescribers."""
    global _medical_profile_upgrade_policy  # noqa: PLW0603
    _medical_profile_upgrade_policy = policy


def transcription_profile_for_user(db: Session, user_id: str) -> TranscriptionProfile:
    """Return medical only for a deployment-approved upgrade."""
    user = db.get(PlatformUserRow, user_id)
    if user is None:
        return "standard"
    if _medical_profile_upgrade_policy is not None and _medical_profile_upgrade_policy(db, user):
        return "medical"
    return "standard"


def reset_medical_profile_upgrade_policy() -> None:
    """Remove the deployment policy for test isolation."""
    global _medical_profile_upgrade_policy  # noqa: PLW0603
    _medical_profile_upgrade_policy = None
