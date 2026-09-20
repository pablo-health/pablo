# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientMessageRepository implementation.

The clinician verbs delegate their access predicate to the schema-local
``has_patient_access`` SQL function (migration ``777b846ab944``), the same
one the notes and outcome-measure repositories use, so the app-layer check
and the row policy underneath it cannot drift apart.

The patient verbs match on ``patient_id`` from the resolved principal. Row
security tests the same pair independently and deliberately: a policy is the
backstop for a query that forgot its filter, and the filter is the backstop
for a schema migrated without its policies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, func, select, text

from ...db.models import PatientMessageRow, PatientMessageThreadRow
from ...models.patient_message import SENDER_PATIENT
from ..patient_message import PatientMessageAccessDeniedError, PatientMessageRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from ...models import PatientMessage, PatientMessageThread


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _thread(row: PatientMessageThreadRow) -> PatientMessageThread:
    from ...models import PatientMessageThread  # noqa: PLC0415 — avoids a cycle at import time

    return PatientMessageThread(
        id=row.id,
        patient_id=row.patient_id,
        subject=row.subject,
        status=row.status,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
    )


def _message(row: PatientMessageRow) -> PatientMessage:
    from ...models import PatientMessage  # noqa: PLC0415 — avoids a cycle at import time

    return PatientMessage(
        id=row.id,
        thread_id=row.thread_id,
        patient_id=row.patient_id,
        sender=row.sender,
        body=row.body,
        created_at=row.created_at,
        read_at=row.read_at,
    )


class PostgresPatientMessageRepository(PatientMessageRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- internal helpers ---

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    def _thread_row(self, thread_id: str) -> PatientMessageThreadRow | None:
        return self._session.get(PatientMessageThreadRow, thread_id)

    def _messages_in(self, thread_id: str) -> list[PatientMessage]:
        rows = (
            self._session.execute(
                select(PatientMessageRow)
                .where(PatientMessageRow.thread_id == thread_id)
                .order_by(PatientMessageRow.created_at.asc())
            )
            .scalars()
            .all()
        )
        return [_message(r) for r in rows]

    def _insert_message(self, message: PatientMessage) -> PatientMessage:
        """Write the row and carry its timestamp onto the parent thread.

        The parent already exists on every path that reaches here, so this
        is an UPDATE rather than the flush-parent-first dance the models
        module describes.
        """
        row = PatientMessageRow(
            id=message.id,
            thread_id=message.thread_id,
            patient_id=message.patient_id,
            sender=message.sender,
            body=message.body,
            created_at=message.created_at,
            read_at=message.read_at,
        )
        self._session.add(row)
        thread_row = self._thread_row(message.thread_id)
        if thread_row is not None:
            thread_row.last_message_at = message.created_at
        self._session.flush()
        return _message(row)

    # --- clinician arm ---

    def list_threads_for_patient(self, patient_id: str, user_id: str) -> list[PatientMessageThread]:
        if not self._has_access(patient_id, user_id):
            return []
        rows = (
            self._session.execute(
                select(PatientMessageThreadRow)
                .where(PatientMessageThreadRow.patient_id == patient_id)
                .order_by(PatientMessageThreadRow.last_message_at.desc())
            )
            .scalars()
            .all()
        )
        return [_thread(r) for r in rows]

    def get_thread(self, thread_id: str, user_id: str) -> PatientMessageThread | None:
        row = self._thread_row(thread_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            return None
        return _thread(row)

    def list_messages(self, thread_id: str, user_id: str) -> list[PatientMessage]:
        if self.get_thread(thread_id, user_id) is None:
            return []
        return self._messages_in(thread_id)

    def add_reply(self, message: PatientMessage, user_id: str) -> PatientMessage:
        row = self._thread_row(message.thread_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            raise PatientMessageAccessDeniedError(message.thread_id, user_id)
        return self._insert_message(message)

    # --- patient arm ---

    def add_patient_thread(
        self, thread: PatientMessageThread, message: PatientMessage
    ) -> tuple[PatientMessageThread, PatientMessage]:
        if message.thread_id != thread.id or message.patient_id != thread.patient_id:
            raise PatientMessageAccessDeniedError(thread.id, message.patient_id)
        thread_row = PatientMessageThreadRow(
            id=thread.id,
            patient_id=thread.patient_id,
            subject=thread.subject,
            status=thread.status,
            created_at=thread.created_at,
            last_message_at=thread.last_message_at,
        )
        self._session.add(thread_row)
        # Flush the parent before the child: there are no ORM relationships
        # in this codebase, so the unit of work cannot see the dependency
        # and would otherwise insert in mapper order (see db/models.py).
        self._session.flush()
        stored = self._insert_message(message)
        return _thread(thread_row), stored

    def get_patient_thread(self, thread_id: str, patient_id: str) -> PatientMessageThread | None:
        row = self._thread_row(thread_id)
        if row is None or row.patient_id != patient_id:
            return None
        return _thread(row)

    def list_patient_threads(self, patient_id: str) -> list[tuple[PatientMessageThread, int]]:
        unread = (
            select(
                PatientMessageRow.thread_id.label("thread_id"),
                func.count().label("unread"),
            )
            .where(
                PatientMessageRow.patient_id == patient_id,
                PatientMessageRow.sender != SENDER_PATIENT,
                PatientMessageRow.read_at.is_(None),
            )
            .group_by(PatientMessageRow.thread_id)
            .subquery()
        )
        rows = self._session.execute(
            select(PatientMessageThreadRow, func.coalesce(unread.c.unread, 0))
            .outerjoin(unread, unread.c.thread_id == PatientMessageThreadRow.id)
            .where(PatientMessageThreadRow.patient_id == patient_id)
            .order_by(PatientMessageThreadRow.last_message_at.desc())
        ).all()
        return [(_thread(row), int(count)) for row, count in rows]

    def list_patient_messages(self, thread_id: str, patient_id: str) -> list[PatientMessage]:
        if self.get_patient_thread(thread_id, patient_id) is None:
            return []
        return self._messages_in(thread_id)

    def add_patient_message(self, message: PatientMessage) -> PatientMessage:
        if self.get_patient_thread(message.thread_id, message.patient_id) is None:
            raise PatientMessageAccessDeniedError(message.thread_id, message.patient_id)
        return self._insert_message(message)

    def mark_thread_read(self, thread_id: str, patient_id: str, read_at: datetime) -> int:
        if self.get_patient_thread(thread_id, patient_id) is None:
            return 0
        rows = (
            self._session.execute(
                select(PatientMessageRow).where(
                    PatientMessageRow.thread_id == thread_id,
                    PatientMessageRow.patient_id == patient_id,
                    PatientMessageRow.sender != SENDER_PATIENT,
                    PatientMessageRow.read_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.read_at = read_at
        self._session.flush()
        return len(rows)
