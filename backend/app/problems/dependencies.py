# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-scoped problem-list dependencies, shared by every route that reads the list."""

from __future__ import annotations

from fastapi import Depends

from ..auth.service import TenantContext, get_tenant_context
from ..repositories import PatientProblemRepository
from ..repositories import get_patient_problem_repository as _problem_repo_factory
from .service import ProblemService


def get_problem_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientProblemRepository:
    """The problem list, scoped to the caller's practice."""
    return _problem_repo_factory()


def get_problem_service(
    repo: PatientProblemRepository = Depends(get_problem_repository),
) -> ProblemService:
    return ProblemService(repo)
