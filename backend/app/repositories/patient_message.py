# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Secure patient messaging repository contracts.

Two principals reach these rows and they are authorized differently, so the
verbs are split rather than sharing a body with a flag:

* **Clinician verbs** take the requesting clinician's ``user_id`` and resolve
  access through ``has_patient_access`` — the ``patient_clinicians`` grant
  table that scopes notes, sessions and documents. A covering or successor
  clinician therefore inherits the correspondence, and a clinician who loses
  the treatment relationship loses it with them.
* **Patient verbs** take the calling patient's id, which comes from the
  resolved principal and never from client input. They match on ownership
  and nothing else.

Threading "which principal is this?" through one set of verbs would put both
authorization models in one body, where the wrong branch is one edit away
and reads as a plausible refactor.

Reads return ``None`` / ``[]`` when the caller has no access, matching the
shape of "it does not exist" so neither surface becomes an existence oracle.
Writes raise :class:`PatientMessageAccessDeniedError`, because silently
no-op'ing a write would hide broken code instead of a row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..models.patient_message import (
    SENDER_PATIENT,
    THREAD_STATUS_CLOSED,
    THREAD_STATUS_OPEN,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ..models import PatientMessage, PatientMessageThread
    from ..models.patient_message import ThreadAssignmentFilter


class PatientMessageAccessDeniedError(Exception):
    """Raised when a write touches a thread the caller has no claim to."""

    def __init__(self, thread_id: str, principal_id: str) -> None:
        super().__init__(f"principal {principal_id!r} may not write thread {thread_id!r}")
        self.thread_id = thread_id
        self.principal_id = principal_id


class PatientMessageRepository(ABC):
    """Abstract base class for secure-messaging data access."""

    # ------------------------------------------------------------------
    # Clinician arm — access via ``has_patient_access``
    # ------------------------------------------------------------------

    @abstractmethod
    def list_threads_for_patient(
        self,
        patient_id: str,
        user_id: str,
        assigned: ThreadAssignmentFilter = "all",
    ) -> list[tuple[PatientMessageThread, int]]:
        """Threads for one patient with the practice's unread count.

        Newest activity first, ``[]`` when denied. ``assigned`` narrows the
        list to the caller's own threads or to the unclaimed ones; it never
        narrows what may be read, so a clinician who filters to ``me`` and
        then opens somebody else's thread by id still gets it.

        Unread here means a message the *patient* sent after
        ``clinician_last_read_at``. A thread nobody at the practice has
        opened counts all of them.
        """

    @abstractmethod
    def get_thread(self, thread_id: str, user_id: str) -> PatientMessageThread | None:
        """One thread the clinician may see, else ``None``."""

    @abstractmethod
    def list_messages(self, thread_id: str, user_id: str) -> list[PatientMessage]:
        """Messages in a thread the clinician may see, oldest first."""

    @abstractmethod
    def add_reply(self, message: PatientMessage, user_id: str) -> PatientMessage:
        """Append a clinician reply and bump the thread's ``last_message_at``.

        A reply into a closed thread reopens it in the same transaction —
        answering somebody is reopening the conversation, and making the
        caller do both would leave a window where the patient has an answer
        they cannot reply to.

        Raises :class:`PatientMessageAccessDeniedError` when the caller has
        no grant on the thread's patient.
        """

    @abstractmethod
    def close_thread(
        self, thread_id: str, user_id: str, closed_at: datetime
    ) -> PatientMessageThread:
        """Close a thread, recording who closed it and when.

        Closing a closed thread leaves the original ``closed_by`` and
        ``closed_at`` alone: the fact worth keeping is who ended the
        conversation, not who pressed the button last.

        Raises :class:`PatientMessageAccessDeniedError` without a grant.
        """

    @abstractmethod
    def reopen_thread(self, thread_id: str, user_id: str) -> PatientMessageThread:
        """Reopen a thread and clear its closure. Idempotent on an open one.

        Raises :class:`PatientMessageAccessDeniedError` without a grant.
        """

    @abstractmethod
    def assign_thread(
        self, thread_id: str, user_id: str, assignee_id: str | None
    ) -> PatientMessageThread:
        """Point a thread at a clinician, or at nobody when ``assignee_id`` is None.

        The assignee is not checked for a grant on the patient. Assignment is
        how a practice says who should answer, and a practice that assigns to
        somebody without access has a staffing problem the store should
        report rather than a write to reject — the assignee still cannot read
        the thread, because the policies never consult this column.

        Raises :class:`PatientMessageAccessDeniedError` without a grant.
        """

    @abstractmethod
    def mark_thread_read_by_clinician(
        self, thread_id: str, user_id: str, read_at: datetime
    ) -> PatientMessageThread:
        """Stamp the practice's "we have seen this" mark on the thread.

        One mark for the practice, not one per clinician — see
        :class:`app.models.patient_message.PatientMessageThread`. Never
        touches the per-message ``read_at``, which is the patient's.

        Raises :class:`PatientMessageAccessDeniedError` without a grant.
        """

    # ------------------------------------------------------------------
    # Patient arm — access by owning the row
    # ------------------------------------------------------------------

    @abstractmethod
    def add_patient_thread(
        self, thread: PatientMessageThread, message: PatientMessage
    ) -> tuple[PatientMessageThread, PatientMessage]:
        """Insert a thread and its opening message together.

        One verb rather than two, because a thread with no message is not a
        state this surface should be able to produce — an interrupted pair
        would show up as an empty conversation nobody can explain.

        Raises :class:`PatientMessageAccessDeniedError` if the message does
        not belong to the thread it is opening.
        """

    @abstractmethod
    def get_patient_thread(self, thread_id: str, patient_id: str) -> PatientMessageThread | None:
        """The thread iff it is this patient's, else ``None``."""

    @abstractmethod
    def list_patient_threads(self, patient_id: str) -> list[tuple[PatientMessageThread, int]]:
        """This patient's threads with their unread count, newest activity first.

        Unread means a message somebody else sent that this patient has not
        marked read — the patient's own messages are never unread to them.
        """

    @abstractmethod
    def list_patient_messages(self, thread_id: str, patient_id: str) -> list[PatientMessage]:
        """Messages in the patient's own thread, oldest first. ``[]`` if not theirs."""

    @abstractmethod
    def add_patient_message(self, message: PatientMessage) -> PatientMessage:
        """Append a patient message and bump the thread's ``last_message_at``.

        Raises :class:`PatientMessageAccessDeniedError` when the thread is
        not the message's patient's.
        """

    @abstractmethod
    def mark_thread_read(self, thread_id: str, patient_id: str, read_at: datetime) -> int:
        """Stamp ``read_at`` on this patient's unread incoming messages.

        Returns how many rows changed, so the route can audit a count
        instead of the words. Touches ``read_at`` and no other column, and
        never touches the patient's own messages.
        """


class InMemoryPatientMessageRepository(PatientMessageRepository):
    """In-memory repository for unit tests.

    Clinician access is a ``(patient_id, user_id)`` set populated through
    :meth:`grant_access`, mirroring ``has_patient_access``. Nothing is
    granted by default: the cross-clinician invariants are the point of this
    table, so tests say out loud who may see what.
    """

    def __init__(self) -> None:
        self._threads: dict[str, PatientMessageThread] = {}
        self._messages: dict[str, list[PatientMessage]] = {}
        self._access: set[tuple[str, str]] = set()

    # --- test setup helpers (mirror has_patient_access semantics) ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def revoke_access(self, patient_id: str, user_id: str) -> None:
        self._access.discard((patient_id, user_id))

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return (patient_id, user_id) in self._access

    def _sorted_messages(self, thread_id: str) -> list[PatientMessage]:
        return sorted(self._messages.get(thread_id, []), key=lambda m: m.created_at)

    def _append(self, message: PatientMessage) -> PatientMessage:
        self._messages.setdefault(message.thread_id, []).append(message)
        thread = self._threads[message.thread_id]
        thread.last_message_at = message.created_at
        return message

    # --- clinician arm ---

    def list_threads_for_patient(
        self,
        patient_id: str,
        user_id: str,
        assigned: ThreadAssignmentFilter = "all",
    ) -> list[tuple[PatientMessageThread, int]]:
        if not self._can_access(patient_id, user_id):
            return []
        rows = [t for t in self._threads.values() if t.patient_id == patient_id]
        if assigned == "me":
            rows = [t for t in rows if t.assigned_user_id == user_id]
        elif assigned == "unassigned":
            rows = [t for t in rows if t.assigned_user_id is None]
        rows.sort(key=lambda t: t.last_message_at, reverse=True)
        return [(t, self._clinician_unread_count(t)) for t in rows]

    def _clinician_unread_count(self, thread: PatientMessageThread) -> int:
        since = thread.clinician_last_read_at
        return sum(
            1
            for m in self._messages.get(thread.id, [])
            if m.sender == SENDER_PATIENT and (since is None or m.created_at > since)
        )

    def _writable_thread(self, thread_id: str, user_id: str) -> PatientMessageThread:
        thread = self._threads.get(thread_id)
        if thread is None or not self._can_access(thread.patient_id, user_id):
            raise PatientMessageAccessDeniedError(thread_id, user_id)
        return thread

    def get_thread(self, thread_id: str, user_id: str) -> PatientMessageThread | None:
        thread = self._threads.get(thread_id)
        if thread is None or not self._can_access(thread.patient_id, user_id):
            return None
        return thread

    def list_messages(self, thread_id: str, user_id: str) -> list[PatientMessage]:
        if self.get_thread(thread_id, user_id) is None:
            return []
        return self._sorted_messages(thread_id)

    def add_reply(self, message: PatientMessage, user_id: str) -> PatientMessage:
        thread = self._writable_thread(message.thread_id, user_id)
        stored = self._append(message)
        if thread.status == THREAD_STATUS_CLOSED:
            thread.status = THREAD_STATUS_OPEN
            thread.closed_at = None
            thread.closed_by = None
        return stored

    def close_thread(
        self, thread_id: str, user_id: str, closed_at: datetime
    ) -> PatientMessageThread:
        thread = self._writable_thread(thread_id, user_id)
        if thread.status != THREAD_STATUS_CLOSED:
            thread.status = THREAD_STATUS_CLOSED
            thread.closed_at = closed_at
            thread.closed_by = user_id
        return thread

    def reopen_thread(self, thread_id: str, user_id: str) -> PatientMessageThread:
        thread = self._writable_thread(thread_id, user_id)
        thread.status = THREAD_STATUS_OPEN
        thread.closed_at = None
        thread.closed_by = None
        return thread

    def assign_thread(
        self, thread_id: str, user_id: str, assignee_id: str | None
    ) -> PatientMessageThread:
        thread = self._writable_thread(thread_id, user_id)
        thread.assigned_user_id = assignee_id
        return thread

    def mark_thread_read_by_clinician(
        self, thread_id: str, user_id: str, read_at: datetime
    ) -> PatientMessageThread:
        thread = self._writable_thread(thread_id, user_id)
        thread.clinician_last_read_at = read_at
        return thread

    # --- patient arm ---

    def add_patient_thread(
        self, thread: PatientMessageThread, message: PatientMessage
    ) -> tuple[PatientMessageThread, PatientMessage]:
        if message.thread_id != thread.id or message.patient_id != thread.patient_id:
            raise PatientMessageAccessDeniedError(thread.id, message.patient_id)
        self._threads[thread.id] = thread
        self._messages.setdefault(thread.id, [])
        self._append(message)
        return thread, message

    def get_patient_thread(self, thread_id: str, patient_id: str) -> PatientMessageThread | None:
        thread = self._threads.get(thread_id)
        if thread is None or thread.patient_id != patient_id:
            return None
        return thread

    def list_patient_threads(self, patient_id: str) -> list[tuple[PatientMessageThread, int]]:
        rows = [t for t in self._threads.values() if t.patient_id == patient_id]
        rows.sort(key=lambda t: t.last_message_at, reverse=True)
        return [(t, self._unread_count(t.id)) for t in rows]

    def _unread_count(self, thread_id: str) -> int:
        return sum(
            1
            for m in self._messages.get(thread_id, [])
            if m.sender != SENDER_PATIENT and m.read_at is None
        )

    def list_patient_messages(self, thread_id: str, patient_id: str) -> list[PatientMessage]:
        if self.get_patient_thread(thread_id, patient_id) is None:
            return []
        return self._sorted_messages(thread_id)

    def add_patient_message(self, message: PatientMessage) -> PatientMessage:
        if self.get_patient_thread(message.thread_id, message.patient_id) is None:
            raise PatientMessageAccessDeniedError(message.thread_id, message.patient_id)
        return self._append(message)

    def mark_thread_read(self, thread_id: str, patient_id: str, read_at: datetime) -> int:
        if self.get_patient_thread(thread_id, patient_id) is None:
            return 0
        changed = 0
        for message in self._messages.get(thread_id, []):
            if message.sender != SENDER_PATIENT and message.read_at is None:
                message.read_at = read_at
                changed += 1
        return changed
