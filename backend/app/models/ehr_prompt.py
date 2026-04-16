# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR system prompt model.

Per-EHR system prompts stored in the database so they can be updated
without redeploying the backend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class EhrPrompt:
    """System prompt for a specific EHR system's navigation agent."""

    ehr_system: str
    system_prompt: str
    version: int
    updated_at: datetime
    updated_by: str
    notes: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EhrPrompt:
        """Create from dictionary."""
        return cls(
            ehr_system=data["ehr_system"],
            system_prompt=data["system_prompt"],
            version=data.get("version", 1),
            updated_at=data["updated_at"],
            updated_by=data.get("updated_by", ""),
            notes=data.get("notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "ehr_system": self.ehr_system,
            "system_prompt": self.system_prompt,
            "version": self.version,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "notes": self.notes,
        }
