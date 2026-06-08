# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the supervision-relationship repository.

Exercises the dataclass round-trips and the repository's session calls
with a ``MagicMock`` session — no database required, so these run in the
plain ``make test`` suite. End-to-end RLS / FK behavior is covered by the
integration suite against a real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

from app.db.models import (
    ComplianceItemRow,
    SupervisionHoursRow,
    SupervisionRelationshipRow,
)
from app.repositories.postgres.supervision import (
    PostgresSupervisionRepository,
    SupervisionHours,
    SupervisionRelationship,
)


def _make_relationship(
    *,
    relationship_id: str | None = None,
    user_id: str = "user-1",
    compliance_item_id: str | None = None,
    relationship_type: str = "physician_delegation",
    supervisor_name: str = "Dr. Pat Lee",
    next_review_date: date | None = date(2027, 1, 1),
    status: str = "active",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> SupervisionRelationship:
    now = created_at or datetime.now(UTC)
    return SupervisionRelationship(
        id=relationship_id or str(uuid.uuid4()),
        user_id=user_id,
        compliance_item_id=compliance_item_id,
        relationship_type=relationship_type,
        supervisor_name=supervisor_name,
        supervisor_credential="MD",
        supervisor_dea="XL1234563",
        supervisor_license="MI-12345",
        state="MI",
        effective_date=date(2026, 1, 1),
        review_cadence_days=365,
        next_review_date=next_review_date,
        authority_ref="REF-1",
        status=status,
        notes=None,
        created_at=now,
        updated_at=updated_at or now,
    )


def _make_hours(
    *,
    hours_id: str | None = None,
    relationship_id: str = "rel-1",
    user_id: str = "user-1",
    logged_date: date = date(2026, 6, 1),
    hours: Decimal | None = None,
    kind: str = "direct",
) -> SupervisionHours:
    now = datetime.now(UTC)
    return SupervisionHours(
        id=hours_id or str(uuid.uuid4()),
        supervision_relationship_id=relationship_id,
        user_id=user_id,
        logged_date=logged_date,
        hours=hours if hours is not None else Decimal("1.50"),
        kind=kind,
        supervisor="Dr. Pat Lee",
        notes=None,
        created_at=now,
        updated_at=now,
    )


class TestSupervisionRelationshipDataclass:
    def test_round_trips_all_fields(self) -> None:
        rel = _make_relationship(relationship_id="rel-1", compliance_item_id="item-1")
        assert rel.id == "rel-1"
        assert rel.compliance_item_id == "item-1"
        assert rel.relationship_type == "physician_delegation"
        assert rel.supervisor_dea == "XL1234563"
        assert rel.review_cadence_days == 365
        assert rel.status == "active"


class TestCreateRelationship:
    def test_adds_row_without_creating_item_when_no_label(self) -> None:
        session = MagicMock()
        repo = PostgresSupervisionRepository(session)
        rel = _make_relationship(relationship_id="rel-1")

        result = repo.create_relationship(rel)

        assert result is rel
        added = session.add.call_args.args[0]
        assert isinstance(added, SupervisionRelationshipRow)
        assert added.id == "rel-1"
        # No compliance item created/linked.
        assert rel.compliance_item_id is None
        for call in session.add.call_args_list:
            assert not isinstance(call.args[0], ComplianceItemRow)

    def test_creates_and_links_review_item_when_label_given(self) -> None:
        session = MagicMock()
        repo = PostgresSupervisionRepository(session)
        rel = _make_relationship(relationship_id="rel-1", next_review_date=date(2027, 3, 1))

        repo.create_relationship(rel, review_item_label="Delegation annual review")

        added_types = [type(c.args[0]) for c in session.add.call_args_list]
        assert ComplianceItemRow in added_types
        assert SupervisionRelationshipRow in added_types
        # The relationship is now linked to the freshly-created item.
        assert rel.compliance_item_id is not None
        item = next(
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], ComplianceItemRow)
        )
        assert item.id == rel.compliance_item_id
        assert item.user_id == rel.user_id
        assert item.due_date == date(2027, 3, 1)
        assert item.item_type == "supervision_review"
        assert item.label == "Delegation annual review"

    def test_does_not_create_item_when_already_linked(self) -> None:
        session = MagicMock()
        repo = PostgresSupervisionRepository(session)
        rel = _make_relationship(compliance_item_id="existing-item")

        repo.create_relationship(rel, review_item_label="ignored")

        for call in session.add.call_args_list:
            assert not isinstance(call.args[0], ComplianceItemRow)
        assert rel.compliance_item_id == "existing-item"


class TestRelationshipQueries:
    def test_get_returns_none_for_other_users_row(self) -> None:
        session = MagicMock()
        row = SupervisionRelationshipRow(id="rel-1", user_id="someone-else")
        session.get.return_value = row
        repo = PostgresSupervisionRepository(session)

        assert repo.get("rel-1", "user-1") is None

    def test_get_returns_mapped_dataclass(self) -> None:
        session = MagicMock()
        now = datetime(2026, 6, 1, tzinfo=UTC)
        row = SupervisionRelationshipRow(
            id="rel-1",
            user_id="user-1",
            compliance_item_id="item-1",
            relationship_type="np_collaborative",
            supervisor_name="Dr. Smith",
            supervisor_credential="DO",
            supervisor_dea=None,
            supervisor_license=None,
            state="MI",
            effective_date="2026-01-01",
            review_cadence_days=365,
            next_review_date="2027-01-01",
            authority_ref=None,
            status="active",
            notes=None,
            created_at=now,
            updated_at=now,
        )
        session.get.return_value = row
        repo = PostgresSupervisionRepository(session)

        result = repo.get("rel-1", "user-1")

        assert result is not None
        assert isinstance(result, SupervisionRelationship)
        assert result.relationship_type == "np_collaborative"
        assert result.compliance_item_id == "item-1"

    def test_list_by_user_filters_and_orders(self) -> None:
        session = MagicMock()
        query = session.query.return_value
        query.filter.return_value.order_by.return_value.all.return_value = []
        repo = PostgresSupervisionRepository(session)

        repo.list_by_user("user-1")

        session.query.assert_called_once_with(SupervisionRelationshipRow)
        query.filter.assert_called_once()
        query.filter.return_value.order_by.assert_called_once()

    def test_delete_returns_false_for_other_users_row(self) -> None:
        session = MagicMock()
        session.get.return_value = SupervisionRelationshipRow(id="rel-1", user_id="other")
        repo = PostgresSupervisionRepository(session)

        assert repo.delete("rel-1", "user-1") is False
        session.delete.assert_not_called()


class TestSupervisionHours:
    def test_add_hours_adds_row_and_flushes(self) -> None:
        session = MagicMock()
        repo = PostgresSupervisionRepository(session)
        entry = _make_hours(hours_id="h-1", hours=Decimal("2.25"))

        result = repo.add_hours(entry)

        assert result is entry
        added = session.add.call_args.args[0]
        assert isinstance(added, SupervisionHoursRow)
        assert added.id == "h-1"
        assert added.hours == Decimal("2.25")
        assert added.kind == "direct"
        session.flush.assert_called_once()

    def test_list_hours_filters_by_relationship_and_user(self) -> None:
        session = MagicMock()
        query = session.query.return_value
        query.filter.return_value.order_by.return_value.all.return_value = []
        repo = PostgresSupervisionRepository(session)

        repo.list_hours("rel-1", "user-1")

        session.query.assert_called_once_with(SupervisionHoursRow)
        query.filter.assert_called_once()
        query.filter.return_value.order_by.assert_called_once()
