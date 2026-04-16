# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging service for HIPAA compliance."""

import logging
from typing import Any

from fastapi import Request

from ..models import Patient, User
from ..models.audit import AuditAction, AuditLogEntry, ResourceType
from ..models.session import TherapySession

logger = logging.getLogger(__name__)


class AuditService:
    """
    Service for logging PHI access and modifications.

    HIPAA requires tracking of all access to Protected Health Information.
    Audit entries are stored as structured log records with a 6-year
    retention period (HIPAA minimum per 45 CFR 164.530(j)).
    """

    def __init__(self, db: Any = None) -> None:
        """
        Initialize audit service.

        Args:
            db: Optional database handle (accepted for backward compatibility
                with tests that pass a mock DB).
        """
        self._db = db

    def _extract_request_context(self, request: Request) -> tuple[str | None, str | None]:
        """Extract IP address and user agent from request."""
        # Get IP address (handle proxy headers)
        ip_address = request.headers.get("X-Forwarded-For")
        if ip_address:
            # X-Forwarded-For can have multiple IPs; take the first one
            ip_address = ip_address.split(",")[0].strip()
        else:
            ip_address = request.client.host if request.client else None

        user_agent = request.headers.get("User-Agent")
        return ip_address, user_agent

    def _persist(self, entry: AuditLogEntry) -> None:
        """Persist an audit log entry.

        If a database handle was provided (e.g. a mock in tests), writes
        there. Otherwise logs as structured JSON for centralized log
        ingestion (Cloud Logging, ELK, etc.).
        """
        if self._db is not None:
            try:
                self._db.collection("audit_logs").document(entry.id).set(entry.to_dict())
                return
            except Exception as e:
                logger.error("Failed to write audit log to DB: %s", e)
                return

        logger.info(
            "audit: %s by user %s on %s/%s",
            entry.action,
            entry.user_id,
            entry.resource_type,
            entry.resource_id,
        )

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
        """
        Log an audit event.

        Args:
            action: The action being performed
            user: The user performing the action
            request: The FastAPI request object
            resource_type: Type of resource being accessed
            resource_id: ID of the resource being accessed
            patient: Optional patient for denormalized name storage
            session: Optional session for denormalized session_id
            changes: Optional dict of field changes for update actions

        Returns:
            The created audit log entry
        """
        ip_address, user_agent = self._extract_request_context(request)

        entry = AuditLogEntry(
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
            action=action.value,
            resource_type=resource_type.value,
            resource_id=resource_id,
            patient_id=patient.id if patient else None,
            patient_name=patient.display_name if patient else None,
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
        """Convenience method for logging patient-related actions."""
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
        """Convenience method for logging session-related actions."""
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
        """Log a patient list action (no specific patient)."""
        ip_address, user_agent = self._extract_request_context(request)

        entry = AuditLogEntry(
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
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
        """Log a session list action (no specific session)."""
        ip_address, user_agent = self._extract_request_context(request)

        entry = AuditLogEntry(
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
            action=AuditAction.SESSION_LISTED.value,
            resource_type=ResourceType.SESSION.value,
            resource_id="list",
            ip_address=ip_address,
            user_agent=user_agent,
            changes={"session_count": session_count},
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
        """Log admin actions (export queue)."""
        ip_address, user_agent = self._extract_request_context(request)

        entry = AuditLogEntry(
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
            action=action.value,
            resource_type=ResourceType.SESSION.value,
            resource_id=resource_id,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
        )

        self._persist(entry)

        return entry


def get_audit_service() -> AuditService:
    """Get audit service instance (FastAPI dependency)."""
    return AuditService()
