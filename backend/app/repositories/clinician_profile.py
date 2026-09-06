# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Clinician profile repository — per-practice profile metadata.

ClinicianProfile lives in the tenant schema (per-practice). It carries
profile metadata (title, credentials, role) that is not PHI but is also
not platform-global — different practices may know the same person
under different titles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ClinicianProfile:
    user_id: str
    practice_id: str
    title: str | None = None
    credentials: str | None = None
    # Structured, multi-value credential titles (e.g. ["PMHNP-BC", "RN"]).
    # Source of truth for the credential set; ``credentials`` above is kept
    # as the joined display string derived from this list.
    credential_titles: list[str] | None = None
    role: str = "clinician"
    joined_at: datetime | None = None
    license_number: str | None = None
    license_state: str | None = None
    dea_number: str | None = None
    npi_number: str | None = None
    taxonomy_code: str | None = None


class ClinicianProfileRepository(ABC):
    """Abstract base for clinician-profile data access."""

    @abstractmethod
    def get(self, user_id: str) -> ClinicianProfile | None:
        """Fetch the profile for ``user_id`` in the current tenant schema."""

    @abstractmethod
    def create(self, profile: ClinicianProfile) -> ClinicianProfile:
        """Insert a new profile row."""

    @abstractmethod
    def update(self, profile: ClinicianProfile) -> ClinicianProfile:
        """Update an existing profile. Falls back to ``create`` if missing."""


class InMemoryClinicianProfileRepository(ClinicianProfileRepository):
    """In-memory implementation for tests and single-tenant dev."""

    def __init__(self) -> None:
        self._profiles: dict[str, ClinicianProfile] = {}

    def get(self, user_id: str) -> ClinicianProfile | None:
        return self._profiles.get(user_id)

    def create(self, profile: ClinicianProfile) -> ClinicianProfile:
        self._profiles[profile.user_id] = profile
        return profile

    def update(self, profile: ClinicianProfile) -> ClinicianProfile:
        self._profiles[profile.user_id] = profile
        return profile
