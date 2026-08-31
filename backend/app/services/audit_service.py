# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging service for HIPAA compliance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..models.audit import (
    ACTOR_COMPONENT_MAX_LENGTH,
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    PHI_FIELD_NAMES,
    AuditAction,
    AuditLogEntry,
    ResourceType,
)
from ..repositories.audit import AuditRepository, InMemoryAuditRepository
from ..request_context import extract_request_context

if TYPE_CHECKING:
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
        # The calendar list audits one of these per appointment it returns
        # (the payload carries the patient's display name), and refetches on
        # every mutation — without coalescing a single busy calendar session
        # would write the same rows dozens of times.
        AuditAction.APPOINTMENT_VIEWED,
    }
)
_COALESCED_READ_ACTION_VALUES: frozenset[str] = frozenset(a.value for a in _COALESCED_READ_ACTIONS)


def _action_value(action: AuditAction | str) -> str:
    """The stored string for an action.

    Typed to accept a plain string as well as the enum: a deployment's own
    overlay defines its action codes in its own enum, and the column is
    free text of bounded width, so the door is deliberately open. Passing
    the enum stays the ordinary case.
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
        from ..settings import get_settings

        ttl = get_settings().audit_read_coalesce_seconds
        if ttl <= 0 or entry.action not in _COALESCED_READ_ACTION_VALUES:
            return False
        try:
            from ..redis_client import get_redis_client

            redis = get_redis_client()
            if redis is None:
                return False
            key = f"audit:seen:{entry.user_id}:{entry.action}:{entry.resource_id}"
            # True when the key was set (first access), None when it already
            # existed (already logged this window) -> skip the duplicate.
            first_time = redis.set(key, "1", nx=True, ex=ttl)
            return first_time is None
        except Exception:
            logger.warning("Audit coalesce check failed; writing row (fail-open)", exc_info=True)
            return False

    def _persist(self, entry: AuditLogEntry) -> None:
        if self._should_skip_duplicate(entry):
            return
        if entry.changes is not None:
            _assert_changes_phi_free(entry.changes)
        if entry.actor_component is not None:
            # A component label is a developer-supplied constant, so a value
            # too long for the column is a bug in the caller, not user input.
            # Truncate rather than raise: losing the tail of a label is a far
            # better outcome than losing the audit row (see the re-raise
            # below — a failed audit write fails the request).
            entry.actor_component = entry.actor_component[:ACTOR_COMPONENT_MAX_LENGTH]
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
        # Postgres write succeeded (system of record). Best-effort dual-write
        # to Cloud Logging for tamper-evident retention. Import is inside the
        # conditional so environments without google-cloud-logging installed
        # (tests, local dev with the flag off) never load it. The outer
        # try/except is belt-and-suspenders defense: the inner function
        # already swallows, but we never want a refactor in that module to
        # turn a mirror-write failure into a request failure.
        from ..settings import get_settings

        if get_settings().audit_dual_write_enabled:
            try:
                from .audit_cloud_logging import write_to_cloud_logging

                write_to_cloud_logging(entry)
            except Exception:
                logger.warning(
                    "Audit dual-write to Cloud Logging failed for entry id=%s "
                    "(Postgres row already persisted)",
                    entry.id,
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
        ``_download_action_for`` in the route layer). The ``category``
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

    def log_appointment_action(
        self,
        action: AuditAction | str,
        user: User,
        request: Request,
        appointment_id: str,
        patient_id: str | None = None,
        changes: dict[str, Any] | None = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
        actor_component: str | None = None,
    ) -> AuditLogEntry:
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
    ) -> list[AuditLogEntry]:
        return self._repo.list_for_user(user_id=user_id, since=since, limit=limit)

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
        request: Request,
    ) -> AuditLogEntry:
        """Record an account-security event keyed by ``user_id`` alone.

        For auth surfaces that mint a session without loading a full ``User``
        — notably the usernameless passkey login, where the caller is only
        known after the assertion verifies. Resource is SELF; the payload
        carries no PHI.
        """
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user_id,
            action=_action_value(action),
            resource_type=ResourceType.SELF.value,
            resource_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
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
