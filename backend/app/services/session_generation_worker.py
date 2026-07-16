# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Off-request SOAP generation for the persist-and-202 upload path.

``upload_session`` persists a ``PROCESSING`` session and enqueues a Cloud Task
instead of running the multi-second LLM generation on the HTTP thread. That
task is delivered back to this service authenticated as the Cloud-Tasks
invoker service account — it does **not** inherit the uploader's tenant
session. So before generating, the worker must scope itself: resolve the
payload ``user_id`` to its tenant schema, set the search_path, and arm the RLS
``app.current_user_id`` GUC. A wrong resolution would flush the note into
another tenant's schema, so the resolution is deliberately explicit and the
generation runs only once a concrete schema is established.

The tenant schema name is intentionally NOT carried in the Cloud Tasks payload
(it can identify a tenant); only the opaque ``user_id`` travels, and the schema
is re-resolved here from the shared platform tables.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..db import arm_current_user_id, get_db_session, set_tenant_schema

if TYPE_CHECKING:
    from ..models import Patient, TherapySession
    from ..models.note import Note
    from .session_service import SessionService

logger = logging.getLogger(__name__)


class UnknownTenantError(Exception):
    """The job's user_id maps to no active tenant — a non-retryable failure."""


def resolve_tenant_schema_for_user(user_id: str) -> str | None:
    """Resolve a Pablo ``user_id`` to its active tenant schema, off-request.

    ``user_id`` → email (platform ``users``) → practice (``email_tenant_mappings``
    → ``practices``) → ``schema_name``, all in the shared platform schema via a
    standalone session. Returns ``None`` if the user, mapping, or practice is
    missing or inactive; the caller treats that as non-retryable (no schema to
    safely write into).
    """
    from ..db import create_standalone_session
    from ..db.platform_models import EmailTenantMappingRow, PlatformUserRow, PracticeRow

    with create_standalone_session() as db:
        user = db.get(PlatformUserRow, user_id)
        if user is None:
            return None
        mapping = db.get(EmailTenantMappingRow, user.email)
        if mapping is None:
            return None
        practice = db.get(PracticeRow, mapping.practice_id)
        if practice is None or not practice.is_active:
            return None
        return practice.schema_name


def run_soap_generation_job(
    *,
    session_id: str,
    user_id: str,
    session_service: SessionService,
    transient_is_terminal: bool = False,
) -> tuple[TherapySession, Patient, Note]:
    """Scope the request session to the job's tenant, then generate the note.

    Resolves the tenant schema from ``user_id`` (NOT from a request token — the
    caller is the Cloud-Tasks service account), points the request-scoped
    session's ``search_path`` at it, and arms the RLS GUC before handing off to
    ``SessionService.generate_session_note``. Returns ``(session, patient, note)``
    so the caller can audit the note's creation against the tenant now in scope.
    Raises :class:`UnknownTenantError` when no active tenant resolves, so the
    route can answer non-retryably rather than letting Cloud Tasks retry a job
    that can never succeed.
    """
    schema = resolve_tenant_schema_for_user(user_id)
    if schema is None:
        raise UnknownTenantError(user_id)

    # Override the schema the middleware defaulted for the SA request, and arm
    # RLS for this user, on the request-scoped session the repos write through.
    session = get_db_session()
    set_tenant_schema(session, schema)
    arm_current_user_id(session, user_id)

    logger.info("soap_generation_job start session=%s schema=%s", session_id, schema)
    result = session_service.generate_session_note(
        session_id=session_id,
        user_id=user_id,
        transient_is_terminal=transient_is_terminal,
    )
    logger.info("soap_generation_job done session=%s", session_id)
    return result
