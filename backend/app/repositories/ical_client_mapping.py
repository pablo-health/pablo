# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firestore repository for iCal client identifier to Pablo patient mappings.

Maps EHR-specific client identifiers (SimplePractice initials like "J.A.",
Sessions Health codes like "SH00001") to Pablo patient IDs. Persisted so
future syncs auto-resolve known clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

COLLECTION = "ical_client_mappings"


@dataclass
class ICalClientMapping:
    """Maps an iCal client identifier to a Pablo patient."""

    user_id: str
    ehr_system: str
    client_identifier: str  # "J.A." or "SH00001"
    patient_id: str
    created_at: str = ""

    @property
    def doc_id(self) -> str:
        return f"{self.user_id}_{self.ehr_system}_{self.client_identifier}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "ehr_system": self.ehr_system,
            "client_identifier": self.client_identifier,
            "patient_id": self.patient_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ICalClientMapping:
        return cls(
            user_id=data["user_id"],
            ehr_system=data["ehr_system"],
            client_identifier=data["client_identifier"],
            patient_id=data["patient_id"],
            created_at=data.get("created_at", ""),
        )


class ICalClientMappingRepository:
    """Stores client identifier to patient mappings in Firestore."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self._collection = db.collection(COLLECTION)

    def get(
        self, user_id: str, ehr_system: str, client_identifier: str
    ) -> ICalClientMapping | None:
        doc_id = f"{user_id}_{ehr_system}_{client_identifier}"
        doc = self._collection.document(doc_id).get()
        if not doc.exists:
            return None
        return ICalClientMapping.from_dict(doc.to_dict())

    def list_by_user(self, user_id: str) -> list[ICalClientMapping]:
        query = self._collection.where(filter=FieldFilter("user_id", "==", user_id))
        return [ICalClientMapping.from_dict(doc.to_dict()) for doc in query.stream()]

    def list_by_source(self, user_id: str, ehr_system: str) -> list[ICalClientMapping]:
        query = self._collection.where(filter=FieldFilter("user_id", "==", user_id)).where(
            filter=FieldFilter("ehr_system", "==", ehr_system)
        )
        return [ICalClientMapping.from_dict(doc.to_dict()) for doc in query.stream()]

    def save(self, mapping: ICalClientMapping) -> None:
        if not mapping.created_at:
            mapping.created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._collection.document(mapping.doc_id).set(mapping.to_dict())

    def delete(self, user_id: str, ehr_system: str, client_identifier: str) -> bool:
        doc_id = f"{user_id}_{ehr_system}_{client_identifier}"
        doc = self._collection.document(doc_id).get()
        if not doc.exists:
            return False
        self._collection.document(doc_id).delete()
        return True
