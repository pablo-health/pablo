# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The per-practice OCR policy: its default, its registration, and the worker's wiring.

The extraction behaviour under a declined or offered policy is pinned in
``test_patient_documents_service.py`` (``TestOcrPolicy``). This module pins
the two ends around it: an install that registers nothing offers OCR to
every practice, and the finalize worker hands the extraction the schema it
resolved, which is what the policy is asked about.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.services import document_finalize_worker
from app.services.document_ocr_policy import (
    OfferOcrEverywhere,
    get_document_ocr_policy,
    register_document_ocr_policy,
)


@pytest.fixture(autouse=True)
def _restore_default() -> Any:
    yield
    register_document_ocr_policy(None)


class _Declines:
    def offers_ocr(self, tenant_schema: str | None) -> bool:
        return False


def test_default_offers_ocr_to_every_practice() -> None:
    policy = get_document_ocr_policy()
    assert isinstance(policy, OfferOcrEverywhere)
    assert policy.offers_ocr("practice_a") is True
    assert policy.offers_ocr(None) is True


def test_a_registered_policy_is_asked_and_none_restores_the_default() -> None:
    declines = _Declines()
    register_document_ocr_policy(declines)
    assert get_document_ocr_policy() is declines

    register_document_ocr_policy(None)
    assert isinstance(get_document_ocr_policy(), OfferOcrEverywhere)


_DOCUMENT: Any = object()


class _RecordingService:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def run_finalize_extraction(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return _DOCUMENT


def test_worker_passes_the_resolved_schema_to_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        document_finalize_worker, "resolve_tenant_schema_for_user", lambda _u: "practice_x"
    )
    monkeypatch.setattr(document_finalize_worker, "get_db_session", object)
    monkeypatch.setattr(document_finalize_worker, "set_tenant_schema", lambda *_a: None)
    monkeypatch.setattr(document_finalize_worker, "arm_current_user_id", lambda *_a: None)
    service = _RecordingService()

    result = document_finalize_worker.run_document_finalize_job(
        document_id="doc-1",
        user_id="user-1",
        document_service=service,  # type: ignore[arg-type]
    )

    assert result is _DOCUMENT
    assert service.kwargs == {
        "document_id": "doc-1",
        "user_id": "user-1",
        "transient_is_terminal": False,
        "tenant_schema": "practice_x",
    }
