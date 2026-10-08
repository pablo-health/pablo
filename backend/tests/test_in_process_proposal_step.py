# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The in-process redraft and dictation runners hand the worker a real proposal step.

They build the worker's service by calling its factory directly, outside
FastAPI, so a dependency left to its default arrives as a ``Depends`` marker
rather than a step, and the redraft fails on it.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.chart_proposals.step import ChartProposalStep
from app.routes import note_redraft as redraft_routes
from app.routes import notes as notes_routes
from app.routes import session_dictations as dictation_routes


@pytest.fixture(autouse=True)
def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (redraft_routes, dictation_routes):
        monkeypatch.setattr(module, "resolve_tenant_schema_for_user", lambda _uid: "practice_x")
        monkeypatch.setattr(module, "tenant_db_session", lambda *_a: nullcontext())
        monkeypatch.setattr(module, "get_user_repository", MagicMock)
        monkeypatch.setattr(module, "get_audit_service", MagicMock)
        for factory in (
            "_session_repo_factory",
            "_patient_repo_factory",
            "_notes_repo_factory",
            "_dictation_repo_factory",
        ):
            monkeypatch.setattr(module, factory, MagicMock)
    monkeypatch.setattr(notes_routes, "_proposal_repo_factory", MagicMock)
    monkeypatch.setattr(notes_routes, "_history_repo_factory", MagicMock)
    monkeypatch.setattr(dictation_routes, "file_storage_from_settings", MagicMock())
    monkeypatch.setattr(dictation_routes, "get_dictation_transcriber", MagicMock())


def test_the_in_process_redraft_gets_a_proposal_step(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(
        redraft_routes,
        "redraft_note_job",
        lambda _job, _req, _inv, service, *_a: seen.append(service),
    )

    redraft_routes._run_redraft_in_process(MagicMock(), MagicMock(), MagicMock())

    assert isinstance(seen[0].proposal_step, ChartProposalStep)


def test_the_in_process_dictation_gets_a_proposal_step(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    monkeypatch.setattr(
        dictation_routes,
        "session_dictation_job",
        lambda _job, _req, _inv, service, *_a: seen.append(service),
    )

    dictation_routes._run_in_process(MagicMock(), MagicMock(), MagicMock())

    assert isinstance(seen[0].redraft_service.proposal_step, ChartProposalStep)
