# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether a practice offers its clients the portal.

The one place that answers the question. Every door into the portal asks it:
the public address, sign-in, refresh, every signed-in request (through the
portal's own resolver), invitations and the notices that carry a link. A
practice with the portal off is refused at all of them, the same way an
unknown practice is.

**No row means off.** See
:class:`~app.db.platform_models.PracticePortalSettingsRow`.

A port with two implementations, like the welcome store: the platform table,
and an in-memory one for tests. Routes depend on
:func:`get_portal_settings_store`; the module-level readers below are for the
places that have no dependency injection to hand (the resolver, and the
signature checks that run before any session exists).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import create_standalone_session
from ..db.platform_models import PracticePortalSettingsRow, PracticeRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PortalSettings:
    """A practice's portal settings, as the rest of the package reads them."""

    enabled: bool
    #: Module names the practice offers; ``None`` means all the deployment serves.
    enabled_modules: tuple[str, ...] | None
    #: When the practice first answered; ``None`` means never asked.
    decided_at: datetime | None


#: What a practice with no row has.
NOT_OFFERED = PortalSettings(enabled=False, enabled_modules=None, decided_at=None)


def _from_row(row: PracticePortalSettingsRow | None) -> PortalSettings:
    if row is None:
        return NOT_OFFERED
    return PortalSettings(
        enabled=row.enabled,
        enabled_modules=None if row.enabled_modules is None else tuple(row.enabled_modules),
        decided_at=row.decided_at,
    )


class PortalSettingsStore(Protocol):
    def get(self, practice_id: str) -> PortalSettings:
        """The practice's settings, or :data:`NOT_OFFERED` when it has none."""

    def set_enabled(self, practice_id: str, *, enabled: bool, by: str) -> PortalSettings:
        """Turn the portal on or off. Records the decision the first time."""


class PlatformPortalSettingsStore:
    def get(self, practice_id: str) -> PortalSettings:
        session = create_standalone_session()
        try:
            return _from_row(session.get(PracticePortalSettingsRow, practice_id))
        finally:
            session.close()

    def set_enabled(self, practice_id: str, *, enabled: bool, by: str) -> PortalSettings:
        now = utc_now()
        session = create_standalone_session()
        try:
            statement = insert(PracticePortalSettingsRow).values(
                practice_id=practice_id,
                enabled=enabled,
                decided_at=now,
                updated_at=now,
                updated_by=by,
            )
            session.execute(
                statement.on_conflict_do_update(
                    index_elements=["practice_id"],
                    set_={
                        "enabled": enabled,
                        # Keep the FIRST answer: this records when the
                        # practice decided, not when it last changed its mind.
                        "decided_at": func.coalesce(PracticePortalSettingsRow.decided_at, now),
                        "updated_at": now,
                        "updated_by": by,
                    },
                )
            )
            session.commit()
            return _from_row(session.get(PracticePortalSettingsRow, practice_id))
        finally:
            session.close()


class InMemoryPortalSettingsStore:
    def __init__(self) -> None:
        self.settings: dict[str, PortalSettings] = {}

    def get(self, practice_id: str) -> PortalSettings:
        return self.settings.get(practice_id, NOT_OFFERED)

    def set_enabled(self, practice_id: str, *, enabled: bool, by: str) -> PortalSettings:  # noqa: ARG002 — matches the port
        current = self.get(practice_id)
        updated = replace(current, enabled=enabled, decided_at=current.decided_at or utc_now())
        self.settings[practice_id] = updated
        return updated


def get_portal_settings_store() -> PortalSettingsStore:
    return PlatformPortalSettingsStore()


# ── readers for the doors that have no dependency injection ─────────────


def portal_enabled_in(session: Session, practice_id: str) -> bool:
    """Whether *practice_id* offers the portal, on a session the caller holds."""
    return _from_row(session.get(PracticePortalSettingsRow, practice_id)).enabled


def portal_enabled_for_practice(practice_id: str) -> bool:
    """Whether *practice_id* offers the portal."""
    session = create_standalone_session()
    try:
        return portal_enabled_in(session, practice_id)
    finally:
        session.close()


def portal_enabled_for_schema(schema: str) -> bool:
    """Whether the practice whose clients live in *schema* offers the portal.

    For the doors that know a schema — from a signed token — and not a
    practice id. An unknown schema is off.
    """
    session = create_standalone_session()
    try:
        enabled = session.execute(
            select(PracticePortalSettingsRow.enabled)
            .join(PracticeRow, PracticeRow.id == PracticePortalSettingsRow.practice_id)
            .where(PracticeRow.schema_name == schema)
        ).scalar_one_or_none()
        return bool(enabled)
    finally:
        session.close()
