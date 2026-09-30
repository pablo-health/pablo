# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Session notes that are written and waiting to be reviewed and signed.

One item per session in ``pending_review``: the note has finished
generating and nobody has signed it. Signing finalizes the session, which
takes it out of that status and out of the Inbox.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.enums import SessionStatus
from ...models.inbox import KIND_NOTE_TO_SIGN, InboxItem
from ...repositories import get_patient_repository, get_session_repository
from ._ids import uuids

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ...models import TherapySession
    from ...repositories import PatientRepository, TherapySessionRepository
    from ..registry import InboxContext

#: The most sessions one read lists; a backlog of notes to sign is short.
AWAITING_LIMIT = 200


class NoteToSignSource:
    kind = KIND_NOTE_TO_SIGN

    def __init__(
        self,
        sessions: Callable[[], TherapySessionRepository] = get_session_repository,
        patients: Callable[[], PatientRepository] = get_patient_repository,
    ) -> None:
        self._sessions = sessions
        self._patients = patients

    def _items(self, ctx: InboxContext, sessions: list[TherapySession]) -> list[InboxItem]:
        names = self._patients().get_multiple(list({s.patient_id for s in sessions}), ctx.user_id)
        return [
            InboxItem(
                kind=KIND_NOTE_TO_SIGN,
                source_id=session.id,
                patient_id=session.patient_id,
                patient_name=patient.display_name
                if (patient := names.get(session.patient_id))
                else None,
                title="Note to review and sign",
                occurred_at=session.session_date,
                href=f"/dashboard/sessions/{session.id}",
                context={
                    "session_id": session.id,
                    "session_date": session.session_date.isoformat(),
                },
            )
            for session in sessions
        ]

    def list_open(self, ctx: InboxContext) -> list[InboxItem]:
        sessions = self._sessions().list_recent_by_status(
            ctx.user_id, SessionStatus.PENDING_REVIEW, limit=AWAITING_LIMIT
        )
        return self._items(ctx, sessions)

    def get_items(self, ctx: InboxContext, source_ids: Iterable[str]) -> list[InboxItem]:
        repo = self._sessions()
        sessions = [
            session
            for session_id in uuids(source_ids)
            if (session := repo.get(session_id, ctx.user_id)) is not None
        ]
        return self._items(ctx, sessions)
