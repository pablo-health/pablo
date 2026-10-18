# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging service for HIPAA compliance."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..models.audit import (
    ACTOR_COMPONENT_MAX_LENGTH,
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_SYSTEM,
    PHI_FIELD_NAMES,
    AuditAction,
    AuditCursor,
    AuditLogEntry,
    ResourceType,
)
from ..repositories.audit import AuditRepository, InMemoryAuditRepository
from ..request_context import extract_request_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from fastapi import Request

    from ..models import Patient, User
    from ..models.session import TherapySession

logger = logging.getLogger(__name__)


# Read-access actions eligible for coalescing. Repeated reads of the SAME
# record by the SAME user within the configured window collapse to a single
# audit row. The *_RESTRICTED document variants are deliberately excluded —
# psychotherapy-notes / therapist-private access keeps full per-read fidelity.
_COALESCED_READ_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        AuditAction.PATIENT_VIEWED,
        AuditAction.SESSION_VIEWED,
        AuditAction.CHAT_CONVERSATION_VIEWED,
        AuditAction.CHAT_CONVERSATION_LIST_VIEWED,
        AuditAction.PATIENT_DOCUMENT_VIEWED,
        # The intake card sits on the chart, so this read fires on every
        # visit to the page and refetches with it. Coalescing keeps one row
        # per clinician per patient per window, which is the granularity the
        # disclosure has anyway: the whole submission list, at once.
        AuditAction.PATIENT_INTAKE_SUBMISSION_VIEWED,
        # The artifacts section of the same card, for the same reason: it
        # fires on every visit to the chart and refetches with it, and the
        # disclosure is the whole form's files at once.
        AuditAction.PATIENT_INTAKE_ARTIFACTS_VIEWED,
        # The calendar list audits one of these per appointment it returns
        # (the payload carries the patient's display name), and refetches on
        # every mutation — without coalescing a single busy calendar session
        # would write the same rows dozens of times.
        AuditAction.APPOINTMENT_VIEWED,
        # The practice's Messages page and its nav badge refetch every minute
        # while open, and the thread list audits one of these per patient it
        # shows (the ungrouped view, one per thread). Without coalescing an
        # open tab would record the same disclosure sixty times an hour —
        # reads nobody made. Opening one thread is recorded the same way.
        AuditAction.PATIENT_MESSAGE_THREAD_VIEWED,
        AuditAction.PATIENT_MESSAGE_UNREAD_COUNTED,
        # The Inbox and its badge poll every minute while open, for the same
        # reason and with the same answer as the Messages rows above.
        AuditAction.INBOX_VIEWED,
        AuditAction.INBOX_COUNTED,
    }
)
_COALESCED_READ_ACTION_VALUES: frozenset[str] = frozenset(a.value for a in _COALESCED_READ_ACTIONS)

# A per-day key is dated, so it only has to outlive its own day.
_CALENDAR_DAY_KEY_TTL_SECONDS = 2 * 24 * 60 * 60


@dataclass(frozen=True)
class CoalesceWindow:
    """How long repeat reads of one record by one user collapse to one audit row.

    Exactly one of the two: ``seconds`` is a fixed window from the first read
    (the same rule as ``audit_read_coalesce_seconds``); ``calendar_day`` is
    one row per record per user per UTC day, whatever the time of the read.
    """

    seconds: int = 0
    calendar_day: bool = False

    def __post_init__(self) -> None:
        if (self.seconds > 0) == self.calendar_day:
            msg = "CoalesceWindow needs exactly one of seconds > 0 or calendar_day"
            raise ValueError(msg)


# Read actions registered with their own window (``register_coalesced_read``).
# Independent of ``audit_read_coalesce_seconds``: registering one action never
# changes how any other action is recorded.
_REGISTERED_WINDOWS: dict[str, CoalesceWindow] = {}


def register_coalesced_read(action: AuditAction | str, window: CoalesceWindow) -> None:
    """Coalesce repeat reads recorded as ``action`` within ``window``.

    For a read that discloses identifiers or short facts, not record content,
    and repeats on every page load: a screen listing who needs attention
    today, say, which would otherwise record the same disclosure each time it
    refreshes. Never register an action that discloses record content; those
    keep one row per read. Applies only where Redis is configured, and fails
    open like the built-in gate: a Redis problem writes every row.
    """
    value = _action_value(action)
    if value in _COALESCED_READ_ACTION_VALUES:
        msg = f"{value} is already coalesced by audit_read_coalesce_seconds"
        raise ValueError(msg)
    _REGISTERED_WINDOWS[value] = window


def _action_value(action: AuditAction | str) -> str:
    """The stored string for an action.

    Typed to accept a plain string as well as the enum: a deployment can
    define its own action codes in its own enum, and the column is free
    text of bounded width, so the door is deliberately open. Passing the
    enum stays the ordinary case.
    """
    return action.value if isinstance(action, AuditAction) else str(action)


class AuditService:
    """Service for logging PHI access and modifications.

    HIPAA § 164.312(b) requires persistent audit records. Writes go through
    an AuditRepository (Postgres in production). Never falls back to stdout
    — a missing repo in production is a configuration bug, not a valid mode.
    """

    def __init__(self, repo: AuditRepository) -> None:
        self._repo = repo
        # Entries collected by :meth:`batched`; None outside a batch.
        self._batch: list[AuditLogEntry] | None = None

    @contextmanager
    def batched(self) -> Iterator[None]:
        """Persist every entry logged inside the block together, on exit.

        For a list view that audits one read per row this turns N Redis
        round-trips, N INSERT flushes and N mirror writes into one of each.
        The rows are the same rows: one per record, same coalescing window,
        and a failed write still fails the request. Only the trip count
        changes. Nested blocks join the outermost one.

        If the block raises, nothing collected is written. The response
        that would have disclosed those records was never sent, so there is
        no access to record; the exception is the record of what happened.
        """
        if self._batch is not None:
            yield
            return
        self._batch = []
        try:
            yield
        except BaseException:
            self._batch = None
            raise
        entries, self._batch = self._batch, None
        self._persist_many(entries)

    @staticmethod
    def _coalesce_ttl() -> int:
        from ..settings import get_settings

        return get_settings().audit_read_coalesce_seconds

    @staticmethod
    def _coalesce_key(entry: AuditLogEntry) -> str:
        return f"audit:seen:{entry.user_id}:{entry.action}:{entry.resource_id}"

    def _coalesce_gate(self, entry: AuditLogEntry) -> tuple[str, int] | None:
        """The Redis key and expiry that gate ``entry``, or ``None`` if it is never coalesced.

        A registered action (``register_coalesced_read``) uses its own window
        whatever ``audit_read_coalesce_seconds`` says. A built-in coalesced
        read uses that setting, and is not coalesced when it is 0. A
        calendar-day window dates its key by the entry's own UTC timestamp,
        so the first read of each day writes its row.
        """
        window = _REGISTERED_WINDOWS.get(entry.action)
        if window is not None:
            if window.calendar_day:
                day = entry.timestamp[:10]
                return f"{self._coalesce_key(entry)}:{day}", _CALENDAR_DAY_KEY_TTL_SECONDS
            return self._coalesce_key(entry), window.seconds
        if entry.action not in _COALESCED_READ_ACTION_VALUES:
            return None
        ttl = self._coalesce_ttl()
        if ttl <= 0:
            return None
        return self._coalesce_key(entry), ttl

    def _should_skip_duplicate(self, entry: AuditLogEntry) -> bool:
        """True if this exact read was already recorded in the active window.

        Uses Redis (the instance backing rate-limit / idle-session) as a
        cross-instance dedup gate: ``SET key 1 NX EX ttl`` is atomic and
        self-expiring, so the first read in a window sets the key and writes
        the row, and repeats within ``ttl`` collapse. The window is FIXED
        (the key expires ``ttl`` after the first access, not extended on
        hits), guaranteeing at least one durable access record per work
        window rather than suppressing for hours of continuous work.

        Fails OPEN: a disabled/absent client or any Redis error falls through
        to writing the row. For audit the safe failure is a duplicate row,
        never a dropped access — the inverse of idle_session's fail-closed
        posture. Redis only decides WHETHER to write; Postgres remains the
        synchronous system-of-record and never stores the event itself.
        """
        gate = self._coalesce_gate(entry)
        if gate is None:
            return False
        key, ttl = gate
        try:
            from ..redis_client import get_redis_client

            redis = get_redis_client()
            if redis is None:
                return False
            # True when the key was set (first access), None when it already
            # existed (already logged this window) -> skip the duplicate.
            first_time = redis.set(key, "1", nx=True, ex=ttl)
            return first_time is None
        except Exception:
            logger.warning("Audit coalesce check failed; writing row (fail-open)", exc_info=True)
            return False

    def _duplicates_in(self, entries: list[AuditLogEntry]) -> list[bool]:
        """The batch form of :meth:`_should_skip_duplicate`: one flag per entry.

        Every coalescable entry's ``SET NX`` goes to Redis in one pipeline,
        so a hundred-row page costs one round-trip. Two reads of the same
        record in one batch collapse the same way two requests would: the
        second ``SET NX`` finds the first one's key. Fails open as a whole;
        a Redis problem writes every row.
        """
        skip = [False] * len(entries)
        gated = [
            (i, gate)
            for i, entry in enumerate(entries)
            if (gate := self._coalesce_gate(entry)) is not None
        ]
        if not gated:
            return skip
        try:
            from ..redis_client import get_redis_client

            redis = get_redis_client()
            if redis is None:
                return skip
            pipe = redis.pipeline()
            for _i, (key, ttl) in gated:
                pipe.set(key, "1", nx=True, ex=ttl)
            for (i, _gate), first_time in zip(gated, pipe.execute(), strict=True):
                skip[i] = first_time is None
        except Exception:
            logger.warning("Audit coalesce check failed; writing rows (fail-open)", exc_info=True)
            return [False] * len(entries)
        return skip

    @staticmethod
    def _prepare(entry: AuditLogEntry) -> None:
        if entry.changes is not None:
            _assert_changes_phi_free(entry.changes)
        if entry.actor_component is not None:
            # A component label is a developer-supplied constant, so a value
            # too long for the column is a bug in the caller, not user input.
            # Truncate rather than raise: losing the tail of a label is a far
            # better outcome than losing the audit row (a failed audit write
            # fails the request).
            entry.actor_component = entry.actor_component[:ACTOR_COMPONENT_MAX_LENGTH]

    def _persist(self, entry: AuditLogEntry) -> None:
        if self._batch is not None:
            self._prepare(entry)
            self._batch.append(entry)
            return
        if self._should_skip_duplicate(entry):
            return
        self._prepare(entry)
        try:
            self._repo.append(entry)
        except Exception:
            # Logging the audit failure is safe (no PHI in the entry itself
            # after the cleanup). Re-raise: a failing audit write must fail
            # the request — a silent miss is a HIPAA gap.
            logger.exception(
                "Failed to persist audit log entry id=%s action=%s", entry.id, entry.action
            )
            raise
        self._mirror([entry])

    def _persist_many(self, entries: list[AuditLogEntry]) -> None:
        skip = self._duplicates_in(entries)
        kept = [e for e, s in zip(entries, skip, strict=True) if not s]
        if not kept:
            return
        try:
            self._repo.append_many(kept)
        except Exception:
            logger.exception(
                "Failed to persist %d audit log entries (first id=%s action=%s)",
                len(kept),
                kept[0].id,
                kept[0].action,
            )
            raise
        self._mirror(kept)

    @staticmethod
    def _mirror(entries: list[AuditLogEntry]) -> None:
        """Best-effort dual-write to Cloud Logging, after Postgres succeeded.

        Postgres is the system of record; Cloud Logging is the tamper-evident
        mirror. The import sits inside the flag check so environments without
        google-cloud-logging installed (tests, local dev with the flag off)
        never load it. The try/except is belt-and-suspenders: the writer
        already swallows, but a refactor there must never turn a mirror-write
        failure into a request failure.
        """
        from ..settings import get_settings

        if not get_settings().audit_dual_write_enabled:
            return
        try:
            from .audit_cloud_logging import write_many_to_cloud_logging, write_to_cloud_logging

            if len(entries) == 1:
                write_to_cloud_logging(entries[0])
            else:
                write_many_to_cloud_logging(entries)
        except Exception:
            logger.warning(
                "Audit dual-write to Cloud Logging failed for %d entries (first id=%s); "
                "Postgres rows already persisted",
                len(entries),
                entries[0].id,
                exc_info=True,
            )

    def log(
        self,
        action: AuditAction | str,
        user: User,
        request: Request | None,
        resource_type: ResourceType,
        resource_id: str,
        patient: Patient | None = None,
        session: TherapySession | None = None,
        changes: dict[str, Any] | None = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
        actor_component: str | None = None,
    ) -> AuditLogEntry:
        """Log an audit event.

        ``request`` is optional so off-request paths (service-to-service
        completion callbacks, Cloud Tasks workers) can audit without a live
        HTTP request; ``extract_request_context`` treats ``None`` as
        no-ip/no-user-agent.

        ``user`` is the principal the row is SCOPED to — the identity the
        write runs under, which for every authenticated path is also who
        acted. ``actor_type`` is what separates the two: a public surface
        passes ``ACTOR_TYPE_ANONYMOUS`` so the row stops claiming its owner
        did the thing.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            actor_type=actor_type,
            actor_component=actor_component,
            action=_action_value(action),
            resource_type=resource_type.value,
            resource_id=resource_id,
            patient_id=patient.id if patient else None,
            session_id=session.id if session else None,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_patient_principal_action(
        self,
        action: AuditAction | str,
        request: Request | None,
        patient_id: str,
        resource_type: ResourceType,
        resource_id: str,
        session_id: str | None = None,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record something a PATIENT did, acting as themselves.

        Distinct from :meth:`log_patient_action`, which records a clinician
        acting ON a patient. Here the patient is the actor, so their id is
        what the row is scoped to — ``user_id`` holds whoever acted, and on
        this path that is the patient. ``patient_id`` carries the subject,
        which for a self-action is the same person; keeping both columns
        populated means a query for "everything about this patient" and a
        query for "everything this actor did" both find the row.

        There is no ``user`` argument because there is no clinician in the
        room. A patient principal arms a different GUC and has no ``User``
        record, which is exactly why :meth:`log` cannot serve this path.

        ``changes`` stays PHI-free like everywhere else: it records that a
        disclosure or a write happened, never what was said.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=patient_id,
            actor_type=ACTOR_TYPE_PATIENT,
            action=_action_value(action),
            resource_type=resource_type.value,
            resource_id=resource_id,
            patient_id=patient_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_patient_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        patient: Patient,
        changes: dict[str, Any] | None = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
        actor_component: str | None = None,
    ) -> AuditLogEntry:
        return self.log(
            action=action,
            user=user,
            request=request,
            resource_type=ResourceType.PATIENT,
            resource_id=patient.id,
            patient=patient,
            changes=changes,
            actor_type=actor_type,
            actor_component=actor_component,
        )

    def log_session_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request | None,
        session: TherapySession,
        patient: Patient | None = None,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        return self.log(
            action=action,
            user=user,
            request=request,
            resource_type=ResourceType.SESSION,
            resource_id=session.id,
            patient=patient,
            session=session,
            changes=changes,
        )

    def log_note_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        note_id: str,
        patient_id: str | None,
        session_id: str | None,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Audit a note-scoped operation.

        Notes are PHI-adjacent. We reuse ``ResourceType.SESSION`` (the
        clinical-content resource family) so admins can run a single
        query against ``resource_type='session'`` to see all reads/writes
        of clinical artifacts. Standalone notes (no session_id) record
        ``session_id=NULL`` and the note's own id under ``resource_id``.
        """
        ip_address, user_agent = extract_request_context(request)
        if changes is not None:
            _assert_changes_phi_free(changes)
        entry = AuditLogEntry(
            user_id=user.id,
            action=_action_value(action),
            resource_type=ResourceType.SESSION.value,
            resource_id=note_id,
            patient_id=patient_id,
            session_id=session_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_chat_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        conversation_id: str,
        patient_id: str,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Audit a chat-conversation lifecycle event (THERAPY-bhv).

        Per design doc §10.4, the chat primitive follows a two-tier audit
        policy: lifecycle events (create/archive/purge/promote/blocked)
        land here; per-turn forensic detail (content, manifest, token
        counts) lives on the ``chat_messages`` row. Callers must keep
        ``changes`` PHI-free — ids, counts, hashes, enum codes only.
        """
        ip_address, user_agent = extract_request_context(request)
        if changes is not None:
            _assert_changes_phi_free(changes)
        entry = AuditLogEntry(
            user_id=user.id,
            action=_action_value(action),
            resource_type=ResourceType.CHAT_CONVERSATION.value,
            resource_id=conversation_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_patient_document_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        document_id: str,
        patient_id: str,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        category: str | None = None,
    ) -> AuditLogEntry:
        """Audit a patient-document lifecycle event (THERAPY-ak6m.2).

        Payload shape is fixed to: document_id (resource_id),
        patient_id, plus optional ``mime_type``, ``size_bytes``, and
        ``category`` carried under ``changes``. We deliberately
        exclude filename and any extracted text — both are PHI-
        adjacent and the audit table must stay PHI-free per
        guardrail #5.

        Note: the *action* itself is split into chart vs. restricted
        variants by the caller (see ``_read_action_for`` /
        ``download_action_for`` in the route layer). The ``category``
        field on the payload disambiguates therapist_private vs.
        psychotherapy_notes within the restricted action.
        """
        ip_address, user_agent = extract_request_context(request)
        changes: dict[str, Any] = {}
        if mime_type is not None:
            changes["mime_type"] = mime_type
        if size_bytes is not None:
            changes["size_bytes"] = size_bytes
        if category is not None:
            changes["category"] = category
        entry = AuditLogEntry(
            user_id=user.id,
            action=_action_value(action),
            resource_type=ResourceType.PATIENT_DOCUMENT.value,
            resource_id=document_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes or None,
        )
        self._persist(entry)
        return entry

    def log_patient_document_ocr(
        self,
        user: User,
        request: Request,
        document_id: str,
        patient_id: str,
        processor: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Audit a Document AI OCR invocation.

        ``outcome`` is ``"success"`` or ``"unavailable"`` so dashboards
        can split the success rate without parsing ``metadata``.
        """
        ip_address, user_agent = extract_request_context(request)
        changes: dict[str, Any] = {"processor": processor, "outcome": outcome}
        if metadata is not None:
            changes.update(metadata)
        entry = AuditLogEntry(
            user_id=user.id,
            action=AuditAction.PATIENT_DOCUMENT_OCR_INVOKED.value,
            resource_type=ResourceType.PATIENT_DOCUMENT.value,
            resource_id=document_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_patient_message_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        resource_id: str,
        patient_id: str,
        resource_type: ResourceType = ResourceType.PATIENT_MESSAGE_THREAD,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record a CLINICIAN acting on secure messaging.

        The patient's own side of the same table goes through
        :meth:`log_patient_principal_action`, which scopes the row to them
        as the actor. This one is the clinician, and the patient the
        correspondence belongs to is the subject.

        ``resource_type`` is a parameter because the two things a clinician
        can open are disclosures of different size. A thread is the content
        read, so it is recorded against the thread. The per-patient index is
        not — it says that correspondence exists and when it last moved — so
        it is recorded against the patient, one row per patient rather than
        one per thread. Same axis the chat surface splits on.

        Takes ids rather than a ``Patient`` because nothing on this surface
        loads one — a thread carries the patient's id and the routes have no
        reason to fetch the record behind it just to write a log line.

        ``changes`` is counts and handles. A subject or a body in here would
        put the words in the compliance record as well as the store.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            actor_type=ACTOR_TYPE_CLINICIAN,
            action=_action_value(action),
            resource_type=resource_type.value,
            resource_id=resource_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_refill_request_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        resource_id: str,
        patient_id: str,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record a CLINICIAN acting on a refill request.

        The patient asking goes through :meth:`log_patient_principal_action`.
        Takes ids for the same reason :meth:`log_patient_message_action` does:
        nothing on this surface loads a ``Patient`` just to write a log line.
        ``changes`` is ids and the decision, never a medication or a note.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            actor_type=ACTOR_TYPE_CLINICIAN,
            action=_action_value(action),
            resource_type=ResourceType.REFILL_REQUEST.value,
            resource_id=resource_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_inbox_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        resource_id: str,
        patient_id: str | None,
        resource_type: ResourceType = ResourceType.INBOX_ITEM,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record a CLINICIAN reading or acting on their Inbox.

        An item is named as ``kind:source_id``; the list is recorded against
        each patient on it. ``changes`` is counts, kinds and dispositions —
        never a title or a message, which is the item's own content.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            actor_type=ACTOR_TYPE_CLINICIAN,
            action=_action_value(action),
            resource_type=resource_type.value,
            resource_id=resource_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_appointment_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request | None,
        appointment_id: str,
        patient_id: str | None = None,
        changes: dict[str, Any] | None = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
        actor_component: str | None = None,
    ) -> AuditLogEntry:
        """``request`` is None when the system acted off-request (calendar sync)."""
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            actor_type=actor_type,
            actor_component=actor_component,
            action=_action_value(action),
            resource_type=ResourceType.APPOINTMENT.value,
            resource_id=appointment_id,
            patient_id=patient_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def list_for_user(
        self,
        user_id: str,
        since: datetime | None = None,
        limit: int = 100,
        before: AuditCursor | None = None,
    ) -> list[AuditLogEntry]:
        return self._repo.list_for_user(user_id=user_id, since=since, limit=limit, before=before)

    def list_for_patient(
        self,
        patient_id: str,
        limit: int = 100,
        before: AuditCursor | None = None,
    ) -> list[AuditLogEntry]:
        return self._repo.list_for_patient(patient_id=patient_id, limit=limit, before=before)

    def log_onboarding_milestone(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record an onboarding milestone (BAA, MFA, security guide, etc.).

        These are compliance events that must be persisted regardless of
        whether the user has accessed any PHI yet. ``changes`` carries
        non-PHI metadata only — version strings, boolean flags.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=_action_value(action),
            resource_type=ResourceType.SELF.value,
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_account_security_event(
        self,
        action: AuditAction | str,
        user_id: str,
        request: Request | None,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Record an account-security event keyed by ``user_id`` alone.

        For auth surfaces that mint a session without loading a full ``User``
        — notably the usernameless passkey login, where the caller is only
        known after the assertion verifies. Resource is SELF; the payload
        carries no PHI. ``request`` is ``None`` when the request did not come
        from the user's own device, so no address or agent is recorded.
        ``changes`` holds facts about the account or device (an enrollment
        outcome, a platform), never anything about a client.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user_id,
            action=_action_value(action),
            resource_type=ResourceType.SELF.value,
            resource_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_self_audit_view(
        self,
        user: User,
        request: Request,
        returned_count: int,
    ) -> AuditLogEntry:
        """Meta-audit: record that the user read their own audit log."""
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=AuditAction.SELF_AUDIT_VIEWED.value,
            resource_type=ResourceType.SELF.value,
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes={"returned_count": returned_count},
        )
        self._persist(entry)
        return entry

    def log_system_action(
        self,
        action: AuditAction | str,
        *,
        scope_user_id: str,
        resource_type: ResourceType,
        resource_id: str,
        actor_component: str,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Log something a job did on its own: no request, nobody signed in.

        ``scope_user_id`` is the principal the row is scoped to, and must be
        the id the write's session is armed with. A job acting on a practice's
        behalf scopes the row to whoever the practice would ask about it.
        """
        entry = AuditLogEntry(
            user_id=scope_user_id,
            actor_type=ACTOR_TYPE_SYSTEM,
            actor_component=actor_component,
            action=_action_value(action),
            resource_type=resource_type.value,
            resource_id=resource_id,
            changes=changes,
        )
        self._persist(entry)
        return entry

    def log_admin_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        resource_id: str = "",
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=_action_value(action),
            resource_type=ResourceType.SESSION.value,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )
        self._persist(entry)
        return entry


def _assert_changes_phi_free(changes: dict[str, Any]) -> None:
    """Callers must not pass PHI values into the `changes` dict. Enforced here.

    Allowed: ``{"changed_fields": [...]}``, ``{"patient_count": 5}``,
    ``{"quality_rating": {"old": 3, "new": 4}}``, ``{"status": "active"}``.
    Rejected: ``{"first_name": {"old": "John", "new": "Jane"}}`` — the key
    ``first_name`` is a PHI field name; use ``changed_fields`` instead.
    """
    for key in changes:
        if key in PHI_FIELD_NAMES:
            msg = (
                f"Audit 'changes' contains PHI field name {key!r}; pass "
                f"{{'changed_fields': [...]}} instead (names only, no values)."
            )
            raise ValueError(msg)


def get_audit_service() -> AuditService:
    """FastAPI dependency — returns a request-scoped AuditService.

    Uses PostgresAuditRepository when a DB session is available (production).
    Falls back to an in-memory repo for dev/test modes that run without
    Postgres (e.g. the pytest unit suite). In-memory mode intentionally
    loses entries on restart — it is never what production should run.
    """
    try:
        from ..db import get_db_session
        from ..repositories.postgres.audit import PostgresAuditRepository

        return AuditService(PostgresAuditRepository(get_db_session()))
    except RuntimeError:
        return AuditService(InMemoryAuditRepository())
