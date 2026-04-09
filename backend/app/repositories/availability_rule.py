# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firestore availability rule repository implementation."""

from __future__ import annotations

from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from ..scheduling_engine.models.availability import AvailabilityRule
from ..scheduling_engine.repositories.availability_rule import AvailabilityRuleRepository


class FirestoreAvailabilityRuleRepository(AvailabilityRuleRepository):
    """Firestore implementation of AvailabilityRuleRepository."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.collection = db.collection("availability_rules")

    def get(self, rule_id: str, user_id: str) -> AvailabilityRule | None:
        doc = self.collection.document(rule_id).get()
        if doc.exists:
            rule = AvailabilityRule.from_dict(doc.to_dict())
            if rule.user_id == user_id:
                return rule
        return None

    def list_by_user(self, user_id: str) -> list[AvailabilityRule]:
        query = self.collection.where(filter=FieldFilter("user_id", "==", user_id)).order_by(
            "created_at"
        )
        return [AvailabilityRule.from_dict(doc.to_dict()) for doc in query.stream()]

    def create(self, rule: AvailabilityRule) -> AvailabilityRule:
        self.collection.document(rule.id).set(rule.to_dict())
        return rule

    def update(self, rule: AvailabilityRule) -> AvailabilityRule:
        self.collection.document(rule.id).set(rule.to_dict())
        return rule

    def delete(self, rule_id: str, user_id: str) -> bool:
        rule = self.get(rule_id, user_id)
        if not rule:
            return False
        self.collection.document(rule_id).delete()
        return True
