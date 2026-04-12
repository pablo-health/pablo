# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL availability rule repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import AvailabilityRuleRow
from ...scheduling_engine.models.availability import AvailabilityRule
from ...scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresAvailabilityRuleRepository(AvailabilityRuleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, rule_id: str, user_id: str) -> AvailabilityRule | None:
        row = self._session.get(AvailabilityRuleRow, rule_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_rule(row)

    def list_by_user(self, user_id: str) -> list[AvailabilityRule]:
        rows = (
            self._session.query(AvailabilityRuleRow)
            .filter(AvailabilityRuleRow.user_id == user_id)
            .order_by(AvailabilityRuleRow.created_at)
            .all()
        )
        return [_row_to_rule(r) for r in rows]

    def create(self, rule: AvailabilityRule) -> AvailabilityRule:
        row = AvailabilityRuleRow()
        _rule_to_row(rule, row)
        self._session.add(row)
        self._session.flush()
        return rule

    def update(self, rule: AvailabilityRule) -> AvailabilityRule:
        row = self._session.get(AvailabilityRuleRow, rule.id)
        if row is None:
            row = AvailabilityRuleRow()
            self._session.add(row)
        _rule_to_row(rule, row)
        self._session.flush()
        return rule

    def delete(self, rule_id: str, user_id: str) -> bool:
        row = self._session.get(AvailabilityRuleRow, rule_id)
        if row is None or row.user_id != user_id:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_rule(row: AvailabilityRuleRow) -> AvailabilityRule:
    return AvailabilityRule(
        id=row.id,
        user_id=row.user_id,
        rule_type=row.rule_type,
        enforcement=row.enforcement,
        params=row.params,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _rule_to_row(rule: AvailabilityRule, row: AvailabilityRuleRow) -> None:
    row.id = rule.id
    row.user_id = rule.user_id
    row.rule_type = rule.rule_type
    row.enforcement = rule.enforcement
    row.params = rule.params
    row.created_at = rule.created_at
    row.updated_at = rule.updated_at
