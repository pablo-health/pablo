# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for /api/supervision.

Uses an in-memory repository stub (no DB required) wired into the FastAPI
dependency graph via ``app.dependency_overrides``, mirroring the pattern
established by ``test_routes_compliance.py``. All tests run in the plain
``make test`` suite — no testcontainers or network access needed.

Integration coverage against a real Postgres (RLS, FK behaviour, the
compliance-item link) runs in CI via the testcontainers suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.repositories import get_supervision_repository
from app.repositories.postgres.supervision import (
    SupervisionHours,
    SupervisionRelationship,
)
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

if TYPE_CHECKING:
    from collections.abc import Generator


# ---------------------------------------------------------------------------
# In-memory stub — mirrors PostgresSupervisionRepository's public surface
# ---------------------------------------------------------------------------


class InMemorySupervisionRepository:
    """DB-free stub for route tests.

    Enforces user-scoping the same way the real repo does: cross-user
    ``get``/``update``/``delete`` return ``None``/``False`` rather than raising.
    """

    def __init__(self) -> None:
        self._relationships: dict[str, SupervisionRelationship] = {}
        self._hours: dict[str, SupervisionHours] = {}

    # -- relationships -------------------------------------------------------

    def create_relationship(
        self,
        relationship: SupervisionRelationship,
        *,
        review_item_label: str | None = None,
    ) -> SupervisionRelationship:
        if (
            review_item_label is not None
            and relationship.compliance_item_id is None
            and relationship.next_review_date is not None
        ):
            relationship.compliance_item_id = str(uuid.uuid4())
        self._relationships[relationship.id] = relationship
        return relationship

    def list_by_user(self, user_id: str) -> list[SupervisionRelationship]:
        return sorted(
            (r for r in self._relationships.values() if r.user_id == user_id),
            key=lambda r: r.created_at,
        )

    def get(self, relationship_id: str, user_id: str) -> SupervisionRelationship | None:
        rel = self._relationships.get(relationship_id)
        if rel is None or rel.user_id != user_id:
            return None
        return rel

    def update(self, relationship: SupervisionRelationship) -> SupervisionRelationship:
        self._relationships[relationship.id] = relationship
        return relationship

    def delete(self, relationship_id: str, user_id: str) -> bool:
        rel = self._relationships.get(relationship_id)
        if rel is None or rel.user_id != user_id:
            return False
        del self._relationships[relationship_id]
        return True

    # -- hours ---------------------------------------------------------------

    def add_hours(self, entry: SupervisionHours) -> SupervisionHours:
        self._hours[entry.id] = entry
        return entry

    def list_hours(self, relationship_id: str, user_id: str) -> list[SupervisionHours]:
        return sorted(
            (
                h
                for h in self._hours.values()
                if h.supervision_relationship_id == relationship_id and h.user_id == user_id
            ),
            key=lambda h: h.logged_date,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def supervision_repo(
    client: TestClient,
) -> Generator[InMemorySupervisionRepository, None, None]:
    """In-memory supervision repo wired into the FastAPI dep graph."""
    repo = InMemorySupervisionRepository()
    app.dependency_overrides[get_supervision_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_supervision_repository, None)


def _seed_relationship(
    repo: InMemorySupervisionRepository,
    user_id: str,
    *,
    relationship_type: str = "physician_delegation",
    supervisor_name: str = "Dr. Smith",
    next_review_date: date | None = None,
    status: str = "active",
) -> SupervisionRelationship:
    now = datetime.now(UTC)
    rel = SupervisionRelationship(
        id=str(uuid.uuid4()),
        user_id=user_id,
        compliance_item_id=None,
        relationship_type=relationship_type,
        supervisor_name=supervisor_name,
        supervisor_credential="MD",
        supervisor_dea=None,
        supervisor_license="MI-12345",
        state="MI",
        effective_date=date(2026, 1, 1),
        review_cadence_days=365,
        next_review_date=next_review_date,
        authority_ref=None,
        status=status,
        notes=None,
        created_at=now,
        updated_at=now,
    )
    repo._relationships[rel.id] = rel
    return rel


def _seed_hours(
    repo: InMemorySupervisionRepository,
    relationship_id: str,
    user_id: str,
    *,
    hours: Decimal | None = None,
    logged_date: date = date(2026, 6, 1),
) -> SupervisionHours:
    now = datetime.now(UTC)
    entry = SupervisionHours(
        id=str(uuid.uuid4()),
        supervision_relationship_id=relationship_id,
        user_id=user_id,
        logged_date=logged_date,
        hours=hours if hours is not None else Decimal("2.00"),
        kind="direct",
        supervisor="Dr. Smith",
        notes=None,
        created_at=now,
        updated_at=now,
    )
    repo._hours[entry.id] = entry
    return entry


# ---------------------------------------------------------------------------
# GET /api/supervision
# ---------------------------------------------------------------------------


class TestListRelationships:
    def test_returns_only_caller_items(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        _seed_relationship(supervision_repo, mock_user_id, supervisor_name="Mine")
        _seed_relationship(supervision_repo, "other-user", supervisor_name="Not mine")

        response = client.get("/api/supervision")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["supervisor_name"] == "Mine"

    def test_returns_empty_list_when_none(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.get("/api/supervision")
        assert response.status_code == 200
        assert response.json() == []

    def test_response_shape(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        _seed_relationship(supervision_repo, mock_user_id)
        body = client.get("/api/supervision").json()
        item = body[0]
        for field in (
            "id",
            "relationship_type",
            "supervisor_name",
            "status",
            "created_at",
            "updated_at",
        ):
            assert field in item, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# POST /api/supervision
# ---------------------------------------------------------------------------


class TestCreateRelationship:
    def test_creates_and_returns_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        response = client.post(
            "/api/supervision",
            json={
                "relationship_type": "np_collaborative",
                "supervisor_name": "Dr. Jones",
                "supervisor_credential": "DO",
                "status": "active",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["relationship_type"] == "np_collaborative"
        assert body["supervisor_name"] == "Dr. Jones"
        assert body["supervisor_credential"] == "DO"
        assert body["compliance_item_id"] is None  # no next_review_date supplied

        stored = supervision_repo.list_by_user(mock_user_id)
        assert len(stored) == 1
        assert stored[0].id == body["id"]

    def test_creates_linked_compliance_item_when_review_date_given(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        response = client.post(
            "/api/supervision",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Dr. Kim",
                "status": "active",
                "next_review_date": "2027-06-01",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["next_review_date"] == "2027-06-01"
        # Stub mirrors the real repo: a compliance_item_id is set when review_label is supplied.
        assert body["compliance_item_id"] is not None

    def test_rejects_malformed_effective_date(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.post(
            "/api/supervision",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Dr. X",
                "status": "active",
                "effective_date": "01/01/2026",  # not ISO
            },
        )
        assert response.status_code == 422  # Pydantic date parsing

    def test_rejects_malformed_next_review_date(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.post(
            "/api/supervision",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Dr. X",
                "status": "active",
                "next_review_date": "06/01/2027",  # not ISO
            },
        )
        assert response.status_code == 422  # Pydantic date parsing

    def test_cross_user_isolation_on_list_after_create(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        # Pre-seed a row for a different user.
        _seed_relationship(supervision_repo, "other-user", supervisor_name="Other")

        client.post(
            "/api/supervision",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Mine",
                "status": "active",
            },
        )

        response = client.get("/api/supervision")
        assert response.status_code == 200
        names = {r["supervisor_name"] for r in response.json()}
        assert names == {"Mine"}


# ---------------------------------------------------------------------------
# PUT /api/supervision/{id}
# ---------------------------------------------------------------------------


class TestUpdateRelationship:
    def test_updates_fields_and_returns_new_state(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.put(
            f"/api/supervision/{rel.id}",
            json={
                "relationship_type": "np_collaborative",
                "supervisor_name": "Updated Name",
                "status": "inactive",
                "notes": "Expired",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["relationship_type"] == "np_collaborative"
        assert body["supervisor_name"] == "Updated Name"
        assert body["status"] == "inactive"
        assert body["notes"] == "Expired"

    def test_returns_404_for_nonexistent_id(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.put(
            f"/api/supervision/{uuid.uuid4()}",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Dr. X",
                "status": "active",
            },
        )
        assert response.status_code == 404

    def test_returns_404_for_other_users_item(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        other = _seed_relationship(supervision_repo, "different-user")
        response = client.put(
            f"/api/supervision/{other.id}",
            json={
                "relationship_type": "physician_delegation",
                "supervisor_name": "Spoof",
                "status": "active",
            },
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/supervision/{id}
# ---------------------------------------------------------------------------


class TestDeleteRelationship:
    def test_deletes_existing_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.delete(f"/api/supervision/{rel.id}")
        assert response.status_code == 204
        assert supervision_repo.list_by_user(mock_user_id) == []

    def test_returns_404_for_other_users_item(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        other = _seed_relationship(supervision_repo, "different-user")
        response = client.delete(f"/api/supervision/{other.id}")
        assert response.status_code == 404

    def test_returns_404_for_missing_id(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.delete(f"/api/supervision/{uuid.uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/supervision/{id}/hours
# ---------------------------------------------------------------------------


class TestListHours:
    def test_returns_entries_and_total(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        _seed_hours(
            supervision_repo,
            rel.id,
            mock_user_id,
            hours=Decimal("2.50"),
            logged_date=date(2026, 6, 1),
        )
        _seed_hours(
            supervision_repo,
            rel.id,
            mock_user_id,
            hours=Decimal("1.00"),
            logged_date=date(2026, 6, 2),
        )

        response = client.get(f"/api/supervision/{rel.id}/hours")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["supervision_relationship_id"] == rel.id
        assert body["entry_count"] == 2
        # Decimal sum: 2.50 + 1.00 = 3.50
        assert Decimal(str(body["total_hours"])) == Decimal("3.50")
        assert len(body["entries"]) == 2

    def test_returns_zero_total_when_no_entries(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.get(f"/api/supervision/{rel.id}/hours")
        assert response.status_code == 200
        body = response.json()
        assert body["entry_count"] == 0
        assert Decimal(str(body["total_hours"])) == Decimal("0")

    def test_returns_404_for_other_users_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        other = _seed_relationship(supervision_repo, "different-user")
        response = client.get(f"/api/supervision/{other.id}/hours")
        assert response.status_code == 404

    def test_returns_404_for_missing_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.get(f"/api/supervision/{uuid.uuid4()}/hours")
        assert response.status_code == 404

    def test_cross_user_hours_isolation(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        """Hours seeded under a different user_id must not appear in the response."""
        rel = _seed_relationship(supervision_repo, mock_user_id)
        # Seed hours that belong to a different user but reference the same relationship_id.
        _seed_hours(supervision_repo, rel.id, "other-user", hours=Decimal("5.00"))

        response = client.get(f"/api/supervision/{rel.id}/hours")
        assert response.status_code == 200
        body = response.json()
        assert body["entry_count"] == 0


# ---------------------------------------------------------------------------
# POST /api/supervision/{id}/hours
# ---------------------------------------------------------------------------


class TestAddHours:
    def test_logs_hours_entry_and_returns_it(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.post(
            f"/api/supervision/{rel.id}/hours",
            json={
                "logged_date": "2026-06-15",
                "hours": "3.50",
                "kind": "direct",
                "supervisor": "Dr. Smith",
                "notes": "Weekly session",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["supervision_relationship_id"] == rel.id
        assert body["logged_date"] == "2026-06-15"
        assert Decimal(str(body["hours"])) == Decimal("3.50")
        assert body["kind"] == "direct"

        stored = supervision_repo.list_hours(rel.id, mock_user_id)
        assert len(stored) == 1
        assert stored[0].id == body["id"]

    def test_returns_404_for_other_users_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        other = _seed_relationship(supervision_repo, "different-user")
        response = client.post(
            f"/api/supervision/{other.id}/hours",
            json={"logged_date": "2026-06-15", "hours": "1.00", "kind": "direct"},
        )
        assert response.status_code == 404

    def test_returns_404_for_missing_relationship(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
    ) -> None:
        response = client.post(
            f"/api/supervision/{uuid.uuid4()}/hours",
            json={"logged_date": "2026-06-15", "hours": "1.00", "kind": "direct"},
        )
        assert response.status_code == 404

    def test_rejects_malformed_logged_date(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.post(
            f"/api/supervision/{rel.id}/hours",
            json={"logged_date": "15-06-2026", "hours": "1.00", "kind": "direct"},
        )
        assert response.status_code == 422  # Pydantic date parsing

    def test_rejects_zero_hours(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        response = client.post(
            f"/api/supervision/{rel.id}/hours",
            json={"logged_date": "2026-06-15", "hours": "0", "kind": "direct"},
        )
        assert response.status_code == 422  # Pydantic gt=0 validation

    def test_accrual_sums_correctly_after_two_entries(
        self,
        client: TestClient,
        supervision_repo: InMemorySupervisionRepository,
        mock_user_id: str,
    ) -> None:
        rel = _seed_relationship(supervision_repo, mock_user_id)
        for h in ("1.50", "2.75"):
            client.post(
                f"/api/supervision/{rel.id}/hours",
                json={"logged_date": "2026-06-15", "hours": h, "kind": "indirect"},
            )

        accrual = client.get(f"/api/supervision/{rel.id}/hours").json()
        assert Decimal(str(accrual["total_hours"])) == Decimal("4.25")
        assert accrual["entry_count"] == 2
