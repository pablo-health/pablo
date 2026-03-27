# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""iCal calendar sync API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from ..auth.service import TenantContext, get_tenant_context
from ..database import get_tenant_firestore_client
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
from ..repositories.appointment import FirestoreAppointmentRepository
from ..repositories.ical_client_mapping import ICalClientMappingRepository
from ..repositories.ical_sync_config import ICalSyncConfigRepository
from ..repositories.patient import FirestorePatientRepository
from ..services.ical_sync_service import ICalSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ical-sync", tags=["ical-sync"])


def _get_service(
    ctx: TenantContext = Depends(get_tenant_context),
) -> ICalSyncService:
    db = get_tenant_firestore_client(ctx.firestore_db)
    return ICalSyncService(
        config_repo=ICalSyncConfigRepository(db),
        appointment_repo=FirestoreAppointmentRepository(db),
        patient_repo=FirestorePatientRepository(db),
        mapping_repo=ICalClientMappingRepository(db),
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
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
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
                UnmatchedEvent(**e) for e in r.unmatched_events
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {ehr_system} connection found",
        )
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


@router.post("/import-clients", response_model=ImportClientsResponse)
async def import_clients(
    ehr_system: str,
    file: UploadFile,
    ctx: TenantContext = Depends(get_tenant_context),
    service: ICalSyncService = Depends(_get_service),
) -> ImportClientsResponse:
    """Import clients from a CSV file or zipped export folder."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file provided",
        )
    content = await file.read()
    result = service.import_clients(ctx.user_id, ehr_system, content, file.filename)
    return ImportClientsResponse(
        imported=result.imported,
        skipped=result.skipped,
        mappings_created=result.mappings_created,
        errors=result.errors,
    )
