# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Off-request document-finalize extraction for the persist-and-202 upload path.

``finalize_upload`` persists a ``pending`` document row and enqueues a Cloud
Task instead of running GCS download + PyMuPDF + Document AI on the HTTP
thread. That task is delivered back to this service authenticated as the
Cloud-Tasks invoker service account — it does **not** inherit the
uploader's tenant session. So, mirroring ``session_generation_worker``, the
worker must scope itself before extracting: resolve the payload ``user_id``
to its tenant schema, set the search_path, and arm the RLS
``app.current_user_id`` GUC.

The tenant schema name is intentionally NOT carried in the Cloud Tasks
payload (it can identify a tenant); only the opaque ``user_id`` travels,
and the schema is re-resolved here from the shared platform tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..db import arm_current_user_id, get_db_session, set_tenant_schema
from .session_generation_worker import UnknownTenantError, resolve_tenant_schema_for_user

if TYPE_CHECKING:
    from ..models import PatientDocument
    from .patient_documents_service import PatientDocumentsService

__all__ = ["UnknownTenantError", "run_document_finalize_job"]


def run_document_finalize_job(
    *,
    document_id: str,
    user_id: str,
    document_service: PatientDocumentsService,
    transient_is_terminal: bool = False,
) -> PatientDocument:
    """Scope the request session to the job's tenant, then run extraction.

    Resolves the tenant schema from ``user_id`` (NOT from a request token —
    the caller is the Cloud-Tasks service account), points the request-
    scoped session's ``search_path`` at it, arms RLS, and hands off to
    :meth:`PatientDocumentsService.run_finalize_extraction`. Raises
    :class:`UnknownTenantError` when no active tenant resolves, so the
    route can answer non-retryably rather than letting Cloud Tasks retry a
    job that can never succeed.
    """
    schema = resolve_tenant_schema_for_user(user_id)
    if schema is None:
        raise UnknownTenantError(user_id)

    session = get_db_session()
    set_tenant_schema(session, schema)
    arm_current_user_id(session, user_id)

    return document_service.run_finalize_extraction(
        document_id=document_id,
        user_id=user_id,
        transient_is_terminal=transient_is_terminal,
    )
