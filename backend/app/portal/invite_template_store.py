# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's invitation wording is kept.

A port with two implementations: the platform table, and an in-memory one for
tests. The routes depend on :func:`get_invite_template_store`, so a test swaps
the whole store rather than patching a session.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import delete, select

from ..db import create_standalone_session
from ..db.platform_models import PortalInviteTemplateRow
from ..utcnow import utc_now
from .invite_email import InviteTemplate


class InviteTemplateStore(Protocol):
    def get(self, practice_id: str) -> InviteTemplate | None:
        """The practice's own wording, or None for the default."""

    def save(self, practice_id: str, template: InviteTemplate) -> None:
        """Keep this wording as the practice's own."""

    def reset(self, practice_id: str) -> None:
        """Go back to the default wording."""


class PlatformInviteTemplateStore:
    def get(self, practice_id: str) -> InviteTemplate | None:
        session = create_standalone_session()
        try:
            row = session.execute(
                select(PortalInviteTemplateRow).where(
                    PortalInviteTemplateRow.practice_id == practice_id
                )
            ).scalar_one_or_none()
            return None if row is None else InviteTemplate(subject=row.subject, body=row.body)
        finally:
            session.close()

    def save(self, practice_id: str, template: InviteTemplate) -> None:
        session = create_standalone_session()
        try:
            row = session.get(PortalInviteTemplateRow, practice_id)
            if row is None:
                row = PortalInviteTemplateRow(practice_id=practice_id)
                session.add(row)
            row.subject = template.subject.strip()
            row.body = template.body.strip()
            row.updated_at = utc_now()
            session.commit()
        finally:
            session.close()

    def reset(self, practice_id: str) -> None:
        session = create_standalone_session()
        try:
            session.execute(
                delete(PortalInviteTemplateRow).where(
                    PortalInviteTemplateRow.practice_id == practice_id
                )
            )
            session.commit()
        finally:
            session.close()


class InMemoryInviteTemplateStore:
    def __init__(self) -> None:
        self.templates: dict[str, InviteTemplate] = {}

    def get(self, practice_id: str) -> InviteTemplate | None:
        return self.templates.get(practice_id)

    def save(self, practice_id: str, template: InviteTemplate) -> None:
        self.templates[practice_id] = InviteTemplate(
            subject=template.subject.strip(), body=template.body.strip()
        )

    def reset(self, practice_id: str) -> None:
        self.templates.pop(practice_id, None)


def get_invite_template_store() -> InviteTemplateStore:
    return PlatformInviteTemplateStore()
