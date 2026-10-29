# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient preference as the application handles it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — dataclass field types
from enum import StrEnum
from typing import Any


class Source(StrEnum):
    """Where a preference came from.

    ``clinician`` typed it on the chart; ``assistant`` was a suggestion the
    clinician confirmed; ``learned`` was proposed from what the clinician did
    and confirmed; ``intake`` came from the client's intake; ``migrated`` was
    carried over from an earlier store.
    """

    CLINICIAN = "clinician"
    ASSISTANT = "assistant"
    LEARNED = "learned"
    INTAKE = "intake"
    MIGRATED = "migrated"


@dataclass
class Preference:
    id: str
    patient_id: str
    domain: str
    kind: str
    source: str
    stated_at: datetime
    created_at: datetime
    updated_at: datetime
    value: dict[str, Any] | None = None
    text: str | None = None
    strength: str | None = None
    source_ref: str | None = None
    confirmed_at: datetime | None = None
    retired_at: datetime | None = None
    retired_by: str | None = None
    created_by: str | None = None
