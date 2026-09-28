# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's portal welcome is kept.

A port with two implementations: the platform table, and an in-memory one for
tests. The routes depend on :func:`get_portal_welcome_store`, so a test swaps
the whole store rather than patching a session.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import delete, select

from ..db import create_standalone_session
from ..db.platform_models import PortalWelcomeRow
from ..utcnow import utc_now
from .welcome import PortalWelcome


class PortalWelcomeStore(Protocol):
    def get(self, practice_id: str) -> PortalWelcome | None:
        """The practice's own welcome, or None for the default."""

    def save(self, practice_id: str, welcome: PortalWelcome) -> None:
        """Keep this welcome as the practice's own."""

    def reset(self, practice_id: str) -> None:
        """Go back to the default welcome."""


class PlatformPortalWelcomeStore:
    def get(self, practice_id: str) -> PortalWelcome | None:
        session = create_standalone_session()
        try:
            row = session.execute(
                select(PortalWelcomeRow).where(PortalWelcomeRow.practice_id == practice_id)
            ).scalar_one_or_none()
            return None if row is None else PortalWelcome(heading=row.heading, body=row.body)
        finally:
            session.close()

    def save(self, practice_id: str, welcome: PortalWelcome) -> None:
        session = create_standalone_session()
        try:
            row = session.get(PortalWelcomeRow, practice_id)
            if row is None:
                row = PortalWelcomeRow(practice_id=practice_id)
                session.add(row)
            row.heading = welcome.heading.strip()
            row.body = welcome.body.strip()
            row.updated_at = utc_now()
            session.commit()
        finally:
            session.close()

    def reset(self, practice_id: str) -> None:
        session = create_standalone_session()
        try:
            session.execute(
                delete(PortalWelcomeRow).where(PortalWelcomeRow.practice_id == practice_id)
            )
            session.commit()
        finally:
            session.close()


class InMemoryPortalWelcomeStore:
    def __init__(self) -> None:
        self.welcomes: dict[str, PortalWelcome] = {}

    def get(self, practice_id: str) -> PortalWelcome | None:
        return self.welcomes.get(practice_id)

    def save(self, practice_id: str, welcome: PortalWelcome) -> None:
        self.welcomes[practice_id] = PortalWelcome(
            heading=welcome.heading.strip(), body=welcome.body.strip()
        )

    def reset(self, practice_id: str) -> None:
        self.welcomes.pop(practice_id, None)


def get_portal_welcome_store() -> PortalWelcomeStore:
    return PlatformPortalWelcomeStore()
