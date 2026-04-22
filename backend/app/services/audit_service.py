# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging service for HIPAA compliance."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..models.audit import PHI_FIELD_NAMES, AuditAction, AuditLogEntry, ResourceType
from ..repositories.audit import AuditRepository, InMemoryAuditRepository
from ..request_context import extract_request_context

if TYPE_CHECKING:
    from datetime import datetime

    from fastapi import Request

    from ..models import Patient, User
    from ..models.session import TherapySession

logger = logging.getLogger(__name__)


class AuditService:
    """Service for logging PHI access and modifications.

    HIPAA § 164.312(b) requires persistent audit records. Writes go through
    an AuditRepository (Postgres in production). Never falls back to stdout
    — a missing repo in production is a configuration bug, not a valid mode.
    """

    def __init__(self, repo: AuditRepository) -> None:
        self._repo = repo

    def _persist(self, entry: AuditLogEntry) -> None:
        if entry.changes is not None:
            _assert_changes_phi_free(entry.changes)
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

    def log(
        self,
        action: AuditAction,
        user: User,
        request: Request,
        resource_type: ResourceType,
        resource_id: str,
        patient: Patient | None = None,
        session: TherapySession | None = None,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        """Log an audit event."""
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=action.value,
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

    def log_patient_action(
        self,
        action: AuditAction,
        user: User,
        request: Request,
        patient: Patient,
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        return self.log(
            action=action,
            user=user,
            request=request,
            resource_type=ResourceType.PATIENT,
            resource_id=patient.id,
            patient=patient,
            changes=changes,
        )

    def log_session_action(
        self,
        action: AuditAction,
        user: User,
        request: Request,
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

    def log_patient_list(
        self,
        user: User,
        request: Request,
        patient_count: int,
    ) -> AuditLogEntry:
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=AuditAction.PATIENT_LISTED.value,
            resource_type=ResourceType.PATIENT.value,
            resource_id="list",
            ip_address=ip_address,
            user_agent=user_agent,
            changes={"patient_count": patient_count},
        )
        self._persist(entry)
        return entry

    def log_session_list(
        self,
        user: User,
        request: Request,
        session_count: int,
    ) -> AuditLogEntry:
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=AuditAction.SESSION_LISTED.value,
            resource_type=ResourceType.SESSION.value,
            resource_id="list",
            ip_address=ip_address,
            user_agent=user_agent,
            changes={"session_count": session_count},
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
        action: AuditAction,
        user: User,
        request: Request,
        resource_id: str = "",
        changes: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        ip_address, user_agent = extract_request_context(request)
        entry = AuditLogEntry(
            user_id=user.id,
            action=action.value,
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
