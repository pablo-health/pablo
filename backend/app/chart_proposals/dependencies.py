# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-scoped chart-proposal dependencies."""

from __future__ import annotations

from fastapi import Depends

from ..auth.service import TenantContext, get_tenant_context
from ..chart_history.dependencies import get_chart_history_repository
from ..medications.repository import (
    MedicationRepository,  # noqa: TC001 — FastAPI resolves at runtime
)
from ..repositories import ChartHistoryRepository, ChartProposalRepository
from ..repositories import get_chart_proposal_repository as _proposal_repo_factory
from ..repositories import get_medication_repository as _medication_repo_factory
from .step import ChartProposalStep


def get_chart_proposal_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> ChartProposalRepository:
    """A note's chart proposals, scoped to the caller's practice."""
    return _proposal_repo_factory()


def get_proposal_medication_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> MedicationRepository:
    """The medication list, which medication proposals are measured against and write."""
    return _medication_repo_factory()  # type: ignore[no-any-return]


def get_chart_proposal_step(
    proposals: ChartProposalRepository = Depends(get_chart_proposal_repository),
    history: ChartHistoryRepository = Depends(get_chart_history_repository),
    medications: MedicationRepository = Depends(get_proposal_medication_repository),
) -> ChartProposalStep:
    """Recomputes a note's proposals when a clinician's edit to it is saved."""
    return ChartProposalStep(proposals, history, medications)
