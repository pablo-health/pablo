# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-scoped patient-preference dependencies."""

from __future__ import annotations

from fastapi import Depends

from ..auth.service import TenantContext, get_tenant_context
from ..repositories import PatientPreferenceRepository
from ..repositories import get_patient_preference_repository as _preference_repo_factory
from .service import PreferenceService


def get_preference_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientPreferenceRepository:
    """Patient preferences, scoped to the caller's practice."""
    return _preference_repo_factory()


def get_preference_service(
    repo: PatientPreferenceRepository = Depends(get_preference_repository),
) -> PreferenceService:
    return PreferenceService(repo)
