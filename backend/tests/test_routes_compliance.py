# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/compliance.

The existing CompliancePanel unit test mocks every hook and the only other
compliance tests cover the template registry and the document repository.
Nothing in the suite exercises the actual route handlers, so a server-side
regression to POST/PUT/list/complete/delete would ship undetected — which
matches what motivated this redesign (the wizard's create flow had no
integration coverage at all). These tests close that gap with an in-memory
repository so they stay fast and DB-free.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

import pytest
from app.auth.service import get_current_user
from app.main import app
from app.models import User
from app.repositories import get_compliance_item_repository
from app.repositories.postgres.compliance_item import ComplianceItem
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

if TYPE_CHECKING:
    from collections.abc import Generator


class InMemoryComplianceItemRepository:
    """Mirrors PostgresComplianceItemRepository's surface.

    Tenant scoping (user_id) is enforced exactly like the real repo: a
    cross-tenant `get`/`update`/`delete` returns ``None``/``False`` rather
    than raising. That matches the production contract the route relies on.
    """

    def __init__(self) -> None:
        self._items: dict[str, ComplianceItem] = {}

    def list_by_user(self, user_id: str) -> list[ComplianceItem]:
        return sorted(
            (i for i in self._items.values() if i.user_id == user_id),
            key=lambda i: i.created_at,
        )

    def get(self, item_id: str, user_id: str) -> ComplianceItem | None:
        item = self._items.get(item_id)
        if item is None or item.user_id != user_id:
            return None
        return item

    def create(self, item: ComplianceItem) -> ComplianceItem:
        self._items[item.id] = item
        return item

    def update(self, item: ComplianceItem) -> ComplianceItem:
        self._items[item.id] = item
        return item

    def delete(self, item_id: str, user_id: str) -> bool:
        item = self._items.get(item_id)
        if item is None or item.user_id != user_id:
            return False
        del self._items[item_id]
        return True


@pytest.fixture
def compliance_repo(
    client: TestClient,
) -> Generator[InMemoryComplianceItemRepository]:
    """In-memory compliance repo wired into the FastAPI dep graph."""
    repo = InMemoryComplianceItemRepository()
    app.dependency_overrides[get_compliance_item_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_compliance_item_repository, None)


def _seed(
    repo: InMemoryComplianceItemRepository,
    user_id: str,
    *,
    item_type: str = "license",
    label: str = "Professional license",
    due_date: date | None = date(2027, 1, 1),
) -> ComplianceItem:
    now = datetime.utcnow()
    item = ComplianceItem(
        id=str(uuid.uuid4()),
        user_id=user_id,
        item_type=item_type,
        label=label,
        due_date=due_date,
        notes=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )
    repo.create(item)
    return item


def _make_user(user_id: str, provider_type: str | None) -> User:
    """Build a minimal User with the given provider_type for dependency overrides."""
    return User(
        id=user_id,
        email="test@example.com",
        name="Test User",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_accepted_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        baa_version="2024-01-01",
        provider_type=provider_type,
    )


class TestListTemplates:
    def test_returns_catalog_for_current_edition(self, client: TestClient) -> None:
        response = client.get("/api/compliance/templates")
        assert response.status_code == 200
        body = response.json()
        # The catalog should be non-empty and include the foundational items
        # the composer's Credentials category depends on.
        types = {t["item_type"] for t in body}
        assert "license" in types
        assert "liability_insurance" in types

    def test_therapist_does_not_see_dea_registration(
        self, client: TestClient, mock_user_id: str
    ) -> None:
        """A therapist provider_type must not receive prescriber-only templates."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, "therapist")
        try:
            response = client.get("/api/compliance/templates")
            assert response.status_code == 200
            types = {t["item_type"] for t in response.json()}
            assert "dea_registration" not in types
            assert "dea_mate_training" not in types
            assert "board_certification" not in types
            # Universal templates still appear.
            assert "license" in types
            assert "npi" in types
        finally:
            # Restore the default override so other tests aren't affected.
            app.dependency_overrides.pop(get_current_user, None)

    def test_prescriber_sees_dea_registration(self, client: TestClient, mock_user_id: str) -> None:
        """A prescriber provider_type must receive prescriber-specific templates."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, "prescriber")
        try:
            response = client.get("/api/compliance/templates")
            assert response.status_code == 200
            types = {t["item_type"] for t in response.json()}
            assert "dea_registration" in types
            assert "dea_mate_training" in types
            assert "board_certification" in types
            # Universal templates still appear.
            assert "license" in types
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_none_provider_type_sees_all_templates(
        self, client: TestClient, mock_user_id: str
    ) -> None:
        """A user whose provider_type is None receives the full catalog (backward compat)."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, None)
        try:
            response = client.get("/api/compliance/templates")
            assert response.status_code == 200
            types = {t["item_type"] for t in response.json()}
            assert "dea_registration" in types
            assert "license" in types
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_response_includes_provider_types_field(self, client: TestClient) -> None:
        """Every template in the response carries a provider_types list."""
        response = client.get("/api/compliance/templates")
        assert response.status_code == 200
        for tmpl in response.json():
            assert "provider_types" in tmpl, (
                f"Template {tmpl['item_type']!r} missing provider_types"
            )
            assert isinstance(tmpl["provider_types"], list)
            assert len(tmpl["provider_types"]) > 0


class TestListItems:
    def test_returns_only_caller_items(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        _seed(compliance_repo, mock_user_id, label="Mine")
        _seed(compliance_repo, "other-user", label="Not mine")

        response = client.get("/api/compliance")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["label"] == "Mine"


class TestCreateItem:
    def test_creates_and_returns_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        response = client.post(
            "/api/compliance",
            json={
                "item_type": "license",
                "label": "NY LMHC",
                "due_date": "2027-06-30",
                "notes": "Renewal cycle",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["item_type"] == "license"
        assert body["label"] == "NY LMHC"
        assert body["due_date"] == "2027-06-30"
        assert body["completed_at"] is None

        # And it round-trips through list_by_user with the correct user_id.
        stored = compliance_repo.list_by_user(mock_user_id)
        assert len(stored) == 1
        assert stored[0].id == body["id"]

    def test_rejects_unknown_item_type(self, client: TestClient) -> None:
        response = client.post(
            "/api/compliance",
            json={
                "item_type": "not_a_real_type",
                "label": "Whatever",
                "due_date": None,
                "notes": None,
            },
        )
        assert response.status_code == 400

    def test_rejects_malformed_due_date(self, client: TestClient) -> None:
        response = client.post(
            "/api/compliance",
            json={
                "item_type": "license",
                "label": "X",
                "due_date": "06/30/2027",  # not ISO
                "notes": None,
            },
        )
        assert response.status_code == 422  # Pydantic date parsing

    def test_multi_instance_allows_multiple_of_same_type(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        # The wizard could only ever create one BAA per pass; the composer
        # explicitly supports "Save & add another". Verify the server doesn't
        # reject a second instance of a multi-instance template.
        for label in ("BAA — Spruce", "BAA — SimplePractice"):
            r = client.post(
                "/api/compliance",
                json={
                    "item_type": "baa",
                    "label": label,
                    "due_date": "2027-09-01",
                    "notes": None,
                },
            )
            assert r.status_code == 201, r.text

        stored = compliance_repo.list_by_user(mock_user_id)
        assert {i.label for i in stored} == {"BAA — Spruce", "BAA — SimplePractice"}


class TestUpdateItem:
    def test_updates_fields_and_returns_new_state(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed(compliance_repo, mock_user_id)
        response = client.put(
            f"/api/compliance/{item.id}",
            json={
                "item_type": "license",
                "label": item.label,
                "due_date": "2028-01-15",
                "notes": "Updated",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["due_date"] == "2028-01-15"
        assert body["notes"] == "Updated"

    def test_returns_404_for_other_users_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
    ) -> None:
        # Belongs to a different user.
        other = _seed(compliance_repo, "different-user")

        response = client.put(
            f"/api/compliance/{other.id}",
            json={
                "item_type": "license",
                "label": "Spoof",
                "due_date": None,
                "notes": None,
            },
        )
        assert response.status_code == 404


class TestProviderTypeGate:
    """POST /api/compliance and PUT /api/compliance/{id} enforce provider_type on create/update."""

    def test_therapist_cannot_create_prescriber_only_item(
        self, client: TestClient, mock_user_id: str
    ) -> None:
        """A therapist must be rejected when creating a prescriber-only item (dea_registration)."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, "therapist")
        try:
            response = client.post(
                "/api/compliance",
                json={
                    "item_type": "dea_registration",
                    "label": "DEA",
                    "due_date": None,
                    "notes": None,
                },
            )
            assert response.status_code == 400
            body = response.json()
            assert body.get("error", {}).get("code") == "PROVIDER_TYPE_GATED"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_prescriber_can_create_prescriber_only_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        """A prescriber must succeed when creating a prescriber-only item (dea_registration)."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, "prescriber")
        try:
            response = client.post(
                "/api/compliance",
                json={
                    "item_type": "dea_registration",
                    "label": "DEA",
                    "due_date": "2027-01-01",
                    "notes": None,
                },
            )
            assert response.status_code == 201, response.text
            assert response.json()["item_type"] == "dea_registration"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_none_provider_type_can_create_any_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        """A user with no provider_type set must not be blocked (backward-compat)."""
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, None)
        try:
            response = client.post(
                "/api/compliance",
                json={
                    "item_type": "dea_registration",
                    "label": "DEA",
                    "due_date": None,
                    "notes": None,
                },
            )
            assert response.status_code == 201, response.text
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_therapist_cannot_update_item_to_prescriber_only_type(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        """PUT must also enforce the provider_type gate, not just POST."""
        item = _seed(compliance_repo, mock_user_id, item_type="license", label="License")
        app.dependency_overrides[get_current_user] = lambda: _make_user(mock_user_id, "therapist")
        try:
            response = client.put(
                f"/api/compliance/{item.id}",
                json={
                    "item_type": "dea_registration",
                    "label": "DEA",
                    "due_date": None,
                    "notes": None,
                },
            )
            assert response.status_code == 400
            assert response.json().get("error", {}).get("code") == "PROVIDER_TYPE_GATED"
        finally:
            app.dependency_overrides.pop(get_current_user, None)


class TestCompleteItem:
    def test_sets_completed_at(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed(compliance_repo, mock_user_id)

        response = client.post(f"/api/compliance/{item.id}/complete")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["completed_at"] is not None

    def test_returns_404_when_missing(self, client: TestClient) -> None:
        response = client.post(
            f"/api/compliance/{uuid.uuid4()}/complete",
        )
        assert response.status_code == 404


class TestDeleteItem:
    def test_deletes_existing_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
        mock_user_id: str,
    ) -> None:
        item = _seed(compliance_repo, mock_user_id)

        response = client.delete(f"/api/compliance/{item.id}")
        assert response.status_code == 204
        assert compliance_repo.list_by_user(mock_user_id) == []

    def test_returns_404_for_other_users_item(
        self,
        client: TestClient,
        compliance_repo: InMemoryComplianceItemRepository,
    ) -> None:
        other = _seed(compliance_repo, "different-user")
        response = client.delete(f"/api/compliance/{other.id}")
        assert response.status_code == 404
