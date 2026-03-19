# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability rule repository interface and in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.availability import AvailabilityRule


class AvailabilityRuleRepository(ABC):
    """Abstract base class for availability rule data access."""

    @abstractmethod
    def get(self, rule_id: str, user_id: str) -> AvailabilityRule | None:
        """Get rule by ID, ensuring it belongs to the user."""

    @abstractmethod
    def list_by_user(self, user_id: str) -> list[AvailabilityRule]:
        """List all rules for a user."""

    @abstractmethod
    def create(self, rule: AvailabilityRule) -> AvailabilityRule:
        """Create a new rule."""

    @abstractmethod
    def update(self, rule: AvailabilityRule) -> AvailabilityRule:
        """Update an existing rule."""

    @abstractmethod
    def delete(self, rule_id: str, user_id: str) -> bool:
        """Delete a rule. Returns True if deleted."""


class InMemoryAvailabilityRuleRepository(AvailabilityRuleRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._rules: dict[str, AvailabilityRule] = {}

    def get(self, rule_id: str, user_id: str) -> AvailabilityRule | None:
        rule = self._rules.get(rule_id)
        if rule and rule.user_id == user_id:
            return rule
        return None

    def list_by_user(self, user_id: str) -> list[AvailabilityRule]:
        return [r for r in self._rules.values() if r.user_id == user_id]

    def create(self, rule: AvailabilityRule) -> AvailabilityRule:
        self._rules[rule.id] = rule
        return rule

    def update(self, rule: AvailabilityRule) -> AvailabilityRule:
        self._rules[rule.id] = rule
        return rule

    def delete(self, rule_id: str, user_id: str) -> bool:
        rule = self.get(rule_id, user_id)
        if not rule:
            return False
        del self._rules[rule_id]
        return True
