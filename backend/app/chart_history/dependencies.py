# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-scoped chart-history dependencies."""

from __future__ import annotations

from fastapi import Depends

from ..auth.service import TenantContext, get_tenant_context
from ..repositories import ChartHistoryRepository
from ..repositories import get_chart_history_repository as _history_repo_factory
from .service import ChartHistoryService


def get_chart_history_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> ChartHistoryRepository:
    """Chart history, scoped to the caller's practice."""
    return _history_repo_factory()


def get_chart_history_service(
    repo: ChartHistoryRepository = Depends(get_chart_history_repository),
) -> ChartHistoryService:
    return ChartHistoryService(repo)
