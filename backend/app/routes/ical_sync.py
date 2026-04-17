# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal calendar sync API routes."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_active_subscription
from ..models.scheduling import (
    ConfigureICalRequest,
    ICalConfigureResponse,
    ICalConnectionStatus,
    ICalStatusResponse,
    ICalSyncResponse,
    ImportClientsResponse,
    ResolveClientRequest,
    UnmatchedEvent,
)
from ..repositories import (
    get_appointment_repository as _appt_repo_factory,
)
from ..repositories import (
    get_ical_client_mapping_repository as _mapping_repo_factory,
)
from ..repositories import (
    get_ical_sync_config_repository as _config_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..services.ical_sync_service import ICalSyncService
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/ical-sync",
    tags=["ical-sync"],
    dependencies=[Depends(require_active_subscription)],
)


def _get_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> ICalSyncService:
    return ICalSyncService(
        config_repo=_config_repo_factory(),
        appointment_repo=_appt_repo_factory(),
        patient_repo=_patient_repo_factory(),
        mapping_repo=_mapping_repo_factory(),
    )


@router.post("/configure", response_model=ICalConfigureResponse)
def configure_ical_feed(
    request: ConfigureICalRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> ICalConfigureResponse:
    """Store and validate an iCal feed URL for calendar sync."""
    try:
        result = service.configure(ctx.user_id, request.ehr_system, request.feed_url)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to configure iCal feed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not fetch iCal feed — verify the URL is correct",
        ) from exc
    return ICalConfigureResponse(
        message="Connected successfully",
        event_count=result.event_count,
        ehr_system=result.ehr_system,
    )


@router.post("/sync", response_model=list[ICalSyncResponse])
def sync_ical_calendar(
    ehr_system: str | None = None,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> list[ICalSyncResponse]:
    """Trigger iCal feed sync for one or all configured sources."""
    results = service.sync(ctx.user_id, ehr_system)
    return [
        ICalSyncResponse(
            created=r.created,
            updated=r.updated,
            deleted=r.deleted,
            unchanged=r.unchanged,
            unmatched_events=[
                UnmatchedEvent(
                    ical_uid=e["ical_uid"],
                    client_identifier=e["client_identifier"],
                    start_at=datetime.fromisoformat(e["start_at"]),
                    ehr_appointment_url=e.get("ehr_appointment_url", ""),
                )
                for e in r.unmatched_events
            ],
            errors=r.errors,
        )
        for r in results
    ]


@router.get("/status", response_model=ICalStatusResponse)
def ical_sync_status(
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> ICalStatusResponse:
    """Get connection status for all configured iCal sources."""
    statuses = service.get_status(ctx.user_id)
    return ICalStatusResponse(
        connections=[
            ICalConnectionStatus(
                ehr_system=s.ehr_system,
                connected=s.connected,
                last_synced_at=s.last_synced_at,
                last_sync_error=s.last_sync_error,
            )
            for s in statuses
        ]
    )


@router.delete("/{ehr_system}")
def disconnect_ical_feed(
    ehr_system: str,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> dict[str, str]:
    """Remove a configured iCal feed URL."""
    if not service.disconnect(ctx.user_id, ehr_system):
        raise NotFoundError(f"No {ehr_system} connection found")
    return {"message": f"Disconnected {ehr_system}"}


@router.post("/resolve-client")
def resolve_client(
    request: ResolveClientRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> dict[str, str]:
    """Manually map a client identifier to a Pablo patient."""
    service.resolve_client(
        ctx.user_id,
        request.ehr_system,
        request.client_identifier,
        request.patient_id,
    )
    return {"message": "Client mapped successfully"}


_ALLOWED_EXTENSIONS = {".csv", ".zip"}


@router.post("/import-clients", response_model=ImportClientsResponse)
async def import_clients(
    ehr_system: str,
    file: UploadFile,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> ImportClientsResponse:
    """Import clients from a CSV file or zipped export folder."""
    if not file.filename:
        raise BadRequestError("No file provided")

    # Validate file extension
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise BadRequestError(
            f"Unsupported file type. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}"
        )

    # Enforce size limit (read in chunks to avoid OOM on huge uploads)
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(64 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds {get_settings().max_upload_mb} MB limit",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    result = service.import_clients(ctx.user_id, ehr_system, content, file.filename)
    return ImportClientsResponse(
        imported=result.imported,
        updated=result.updated,
        skipped=result.skipped,
        mappings_created=result.mappings_created,
        errors=result.errors,
    )
