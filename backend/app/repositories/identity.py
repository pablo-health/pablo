# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""User-identity repository — maps an external auth provider subject to
a Pablo-internal user_id.

This indirection is what lets us migrate off Identity Platform (or add
a second provider) without rewriting every user_id FK in every tenant
schema. Auth code looks up the (provider, subject_id) pair; everything
downstream uses the returned Pablo user_id.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from ..utcnow import utc_now


class IdentityRepository(ABC):
    """Abstract base class for user-identity data access."""

    @abstractmethod
    def get_user_id(self, provider: str, subject_id: str) -> str | None:
        """Return the Pablo user_id for an existing identity, or None."""

    @abstractmethod
    def get_subject_id(self, user_id: str, provider: str) -> str | None:
        """Return the provider-specific subject_id for a Pablo user_id, or None.

        Reverse direction of ``get_user_id``. Lets a handler that has a
        ``User`` (Pablo-internal) make an Admin SDK call against the
        external provider without falling back to an email lookup. A
        miss means the Pablo user has no identity registered with this
        provider — different from "the provider doesn't know that uid",
        and worth surfacing as a server-side invariant violation.
        """

    @abstractmethod
    def link(
        self,
        provider: str,
        subject_id: str,
        user_id: str,
        linked_at: datetime | None = None,
    ) -> None:
        """Insert a (provider, subject_id) -> user_id mapping.

        Raises if the (provider, subject_id) pair already exists — callers
        should call ``get_user_id`` first and only link if missing.
        """

    def resolve_or_create(self, provider: str, subject_id: str) -> str:
        """Return the existing user_id for this identity, or create one.

        On miss, generates a fresh UUID, links it, and returns it. The
        new UUID is the Pablo-internal user_id; the caller is expected
        to use it for any subsequent user-record operations.
        """
        existing = self.get_user_id(provider, subject_id)
        if existing is not None:
            return existing
        new_user_id = str(uuid.uuid4())
        self.link(provider, subject_id, new_user_id, linked_at=utc_now())
        return new_user_id


class InMemoryIdentityRepository(IdentityRepository):
    """In-memory implementation for tests and development."""

    def __init__(self) -> None:
        self._mappings: dict[tuple[str, str], str] = {}
        self._linked_at: dict[tuple[str, str], datetime] = {}

    def get_user_id(self, provider: str, subject_id: str) -> str | None:
        return self._mappings.get((provider, subject_id))

    def get_subject_id(self, user_id: str, provider: str) -> str | None:
        for (mapped_provider, subject_id), mapped_user_id in self._mappings.items():
            if mapped_user_id == user_id and mapped_provider == provider:
                return subject_id
        return None

    def link(
        self,
        provider: str,
        subject_id: str,
        user_id: str,
        linked_at: datetime | None = None,
    ) -> None:
        key = (provider, subject_id)
        if key in self._mappings:
            raise ValueError(
                f"identity already linked: provider={provider} subject_id={subject_id}"
            )
        self._mappings[key] = user_id
        self._linked_at[key] = linked_at or utc_now()
