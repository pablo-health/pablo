# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A clinician who does not own the practice is cleared by the practice's
acceptance of the agreement, and is shown a one-time notice, not the agreement."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.service import get_baa_version, require_active_subscription, require_baa_acceptance
from app.main import app
from app.models import User
from app.repositories import InMemoryUserRepository
from app.repositories.practice_admin import InMemoryPracticeAdminRepository
from fastapi import HTTPException

ACCEPTED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _practice(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": "practice-1",
        "owner_email": "owner@example.com",
        "name": "Center for Wellness",
        "address": "5 Oak Ave",
        "baa_accepted_at": ACCEPTED_AT,
        "baa_version": "2024-01-01",
        "baa_legal_name": "Olivia Owner",
        "baa_license_number": "LIC1",
        "baa_license_state": "MI",
        "baa_practice_name": "Center for Wellness",
        "baa_business_address": "5 Oak Ave",
        "baa_full_text": "OWNER SIGNED TEXT",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


@contextmanager
def _in_practice(practice: SimpleNamespace | None) -> Iterator[None]:
    session = MagicMock()
    session.get.return_value = practice
    resolved = ("practice-1", "practice_1") if practice is not None else None
    with (
        patch("app.auth.service._resolve_practice", return_value=resolved),
        patch("app.db.get_db_session", return_value=session),
    ):
        yield


@contextmanager
def _baa_required() -> Iterator[None]:
    with patch("app.auth.service.get_settings", return_value=SimpleNamespace(require_baa=True)):
        yield


def _code(refused: HTTPException) -> str:
    detail: Any = refused.detail
    return str(detail["error"]["code"])


def _member(mock_user: User) -> User:
    mock_user.baa_accepted_at = None
    mock_user.baa_version = None
    return mock_user


class TestGate:
    def test_member_is_cleared_by_the_practice_acceptance(self, mock_user: User) -> None:
        member = _member(mock_user)
        with _baa_required(), _in_practice(_practice()):
            assert require_baa_acceptance(member) is member

    def test_member_waits_with_a_distinct_code_until_the_practice_accepts(
        self, mock_user: User
    ) -> None:
        with (
            _baa_required(),
            _in_practice(_practice(baa_accepted_at=None)),
            pytest.raises(HTTPException) as refused,
        ):
            require_baa_acceptance(_member(mock_user))
        assert refused.value.status_code == 403
        assert _code(refused.value) == "PRACTICE_AGREEMENT_PENDING"

    @pytest.mark.parametrize("owner_email", ["test@example.com", ""])
    def test_owner_still_needs_their_own_acceptance(
        self, mock_user: User, owner_email: str
    ) -> None:
        with (
            _baa_required(),
            _in_practice(_practice(owner_email=owner_email)),
            pytest.raises(HTTPException) as refused,
        ):
            require_baa_acceptance(_member(mock_user))
        assert _code(refused.value) == "BAA_NOT_ACCEPTED"

    def test_no_practice_still_needs_own_acceptance(self, mock_user: User) -> None:
        with _baa_required(), _in_practice(None), pytest.raises(HTTPException) as refused:
            require_baa_acceptance(_member(mock_user))
        assert _code(refused.value) == "BAA_NOT_ACCEPTED"

    def test_a_chart_route_returns_the_waiting_code(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        member = _member(mock_user)
        mock_user_repo.update(member)
        app.dependency_overrides.pop(require_baa_acceptance, None)
        app.dependency_overrides[require_active_subscription] = lambda: member
        try:
            with _baa_required(), _in_practice(_practice(baa_accepted_at=None)):
                response = client.get("/api/patients")
        finally:
            app.dependency_overrides.pop(require_active_subscription, None)
        assert response.status_code == 403
        # The same envelope BAA_NOT_ACCEPTED is sent in.
        assert response.json()["detail"]["error"]["code"] == "PRACTICE_AGREEMENT_PENDING"


class TestStatusAndNotice:
    def _status(self, client: Any) -> dict[str, Any]:
        app.dependency_overrides[get_baa_version] = lambda: "2026-09-26"
        try:
            return dict(client.get("/api/users/me/baa-status").json())
        finally:
            app.dependency_overrides.pop(get_baa_version, None)

    def test_member_status_reports_the_practice_acceptance(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(_member(mock_user))
        with _in_practice(_practice()):
            body = self._status(client)
        assert body["is_practice_owner"] is False
        assert body["may_accept_new_versions"] is False
        assert body["practice_version"] == "2024-01-01"
        assert body["practice_accepted_by"] == "Olivia Owner"
        assert body["practice_accepted_at"] is not None
        assert body["notice_seen_at"] is None

    def test_admin_status_may_accept_new_versions(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        practice_admins: InMemoryPracticeAdminRepository,
    ) -> None:
        mock_user_repo.update(_member(mock_user))
        practice_admins.grant(mock_user.id, granted_by="owner", granted_at=ACCEPTED_AT)
        with _in_practice(_practice()):
            body = self._status(client)
        assert body["is_practice_owner"] is False
        assert body["may_accept_new_versions"] is True

    def test_owner_status_is_unchanged(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        with _in_practice(_practice(owner_email="test@example.com")):
            body = self._status(client)
        assert body["is_practice_owner"] is True
        assert body["accepted"] is True
        assert body["version"] == "2024-01-01"
        assert body["practice_accepted_by"] is None

    def test_notice_is_recorded_once(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(_member(mock_user))
        with (
            _in_practice(_practice()),
            patch("app.services.audit_service.AuditService.log_onboarding_milestone") as audit,
        ):
            first = client.post("/api/users/me/practice-agreement-notice")
            second = client.post("/api/users/me/practice-agreement-notice")
        assert first.status_code == 200, first.text
        assert first.json()["notice_seen_at"] is not None
        assert second.json()["notice_seen_at"] == first.json()["notice_seen_at"]
        audit.assert_called_once()
        assert audit.call_args.args[0] == "onboarding_practice_agreement_notice_seen"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.practice_agreement_notice_seen_at is not None
        assert stored.baa_accepted_at is None

    def test_notice_refused_before_the_practice_accepts(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(_member(mock_user))
        with _in_practice(_practice(baa_accepted_at=None)):
            response = client.post("/api/users/me/practice-agreement-notice")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PRACTICE_AGREEMENT_PENDING"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.practice_agreement_notice_seen_at is None

    def test_owner_is_not_shown_the_notice(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        with _in_practice(_practice(owner_email="test@example.com")):
            response = client.post("/api/users/me/practice-agreement-notice")
        assert response.status_code == 400


CURRENT = "2026-09-26"


@contextmanager
def _accepting(practice: SimpleNamespace) -> Iterator[None]:
    app.dependency_overrides[get_baa_version] = lambda: CURRENT
    try:
        with _in_practice(practice), patch("app.routes.users._resolve_baa_path") as baa_path:
            baa_path.return_value.read_text.return_value = "NEW TEXT"
            yield
    finally:
        app.dependency_overrides.pop(get_baa_version, None)


def _accept(client: Any, version: str = CURRENT) -> Any:
    return client.post("/api/users/me/accept-baa", json={"accepted": True, "version": version})


def _admin(mock_user: User, practice_admins: InMemoryPracticeAdminRepository) -> User:
    mock_user.legal_name = "Ada Admin"
    practice_admins.grant(mock_user.id, granted_by="owner", granted_at=ACCEPTED_AT)
    return mock_user


def test_a_member_cannot_accept_for_a_practice_that_has_not(
    client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
) -> None:
    """Their acceptance would stand in for the practice's and skip the waiting state."""
    mock_user_repo.update(_member(mock_user))
    practice = _practice(baa_accepted_at=None, baa_version=None)
    with _accepting(practice):
        response = _accept(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PRACTICE_AGREEMENT_PENDING"
    assert practice.baa_accepted_at is None
    stored = mock_user_repo.get(mock_user.id)
    assert stored is not None
    assert stored.baa_accepted_at is None
    with _baa_required(), _in_practice(practice), pytest.raises(HTTPException) as refused:
        require_baa_acceptance(stored)
    assert _code(refused.value) == "PRACTICE_AGREEMENT_PENDING"


def test_a_member_accepting_after_the_practice_stamps_only_their_row(
    client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
) -> None:
    mock_user_repo.update(_member(mock_user))
    practice = _practice()
    with _accepting(practice):
        response = _accept(client)
    assert response.status_code == 200, response.text
    assert practice.baa_version == "2024-01-01"
    assert practice.baa_legal_name == "Olivia Owner"


@pytest.mark.parametrize(
    ("practice_version", "accepted_version"),
    [(CURRENT, CURRENT), ("2024-01-01", "2023-01-01")],
    ids=["same-version", "older-version"],
)
def test_an_admin_does_not_replace_the_practice_record_without_a_new_version(
    client: Any,
    mock_user: User,
    mock_user_repo: InMemoryUserRepository,
    practice_admins: InMemoryPracticeAdminRepository,
    practice_version: str,
    accepted_version: str,
) -> None:
    mock_user_repo.update(_admin(mock_user, practice_admins))
    practice = _practice(baa_version=practice_version)
    with _accepting(practice):
        response = _accept(client, accepted_version)
    assert response.status_code == 200, response.text
    assert practice.baa_version == practice_version
    assert practice.baa_legal_name == "Olivia Owner"
    assert practice.baa_full_text == "OWNER SIGNED TEXT"
    assert practice.baa_accepted_at == ACCEPTED_AT
    stored = mock_user_repo.get(mock_user.id)
    assert stored is not None
    assert stored.baa_version == accepted_version


def test_an_admin_acceptance_of_a_new_version_clears_the_practice(
    client: Any,
    mock_user: User,
    mock_user_repo: InMemoryUserRepository,
    practice_admins: InMemoryPracticeAdminRepository,
) -> None:
    """Only the owner or an admin is asked to accept a new version; the
    practice's snapshot records who accepted it."""
    mock_user_repo.update(_admin(mock_user, practice_admins))
    practice = _practice()
    with _accepting(practice):
        response = _accept(client)
    assert response.status_code == 200, response.text
    assert practice.baa_version == CURRENT
    assert practice.baa_legal_name == "Ada Admin"
    assert practice.baa_full_text == "NEW TEXT"
