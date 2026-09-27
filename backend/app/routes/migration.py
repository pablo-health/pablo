# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Import a records-system export into this practice.

Upload an archive, read the preview once it is ready, answer its questions,
apply, and — if it was the wrong archive — undo. Every route is scoped to the
signed-in clinician's practice and audited by handle: run ids, counts and
states, never a name, a file name or anything read from the archive.

Preview and apply are background steps (see :mod:`app.migration.jobs`): the
POST returns 202 with the run, and the screen polls the run until its state
moves on. Nothing here waits on an archive being read.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import get_db_session, release_db_connection
from ..migration.apply import ArchiveApplier
from ..migration.jobs import archive_base, run_apply, run_preview
from ..migration.ledger import (
    ArchiveError,
    archive_path,
    delete_archive,
    get_run,
    list_runs,
    new_run,
    store_archive,
    sweep_expired_archives,
)
from ..migration.preview import decisions_complete
from ..migration.readers.simplepractice import SOURCE_SYSTEM
from ..models import User
from ..models.audit import ResourceType
from ..services import AuditService, get_audit_service
from ..settings import get_settings
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/migration", tags=["migration"])

#: An export zip larger than this is refused at upload.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
_READ_CHUNK = 1024 * 1024

Scope = Literal["patients", "practice", "both"]


class ImportRunSummary(BaseModel):
    id: str
    source_system: str
    scope: str
    state: str
    started_at: datetime
    finished_at: datetime | None
    archive_expires_at: datetime | None
    has_archive: bool
    counts: dict[str, dict[str, int]] | None
    error: str | None


class ImportRunDetail(ImportRunSummary):
    preview: dict[str, Any] | None
    decisions: dict[str, Any] | None
    report: dict[str, Any] | None
    missing: list[str] = Field(default_factory=list)


class ImportRunList(BaseModel):
    runs: list[ImportRunSummary]


class ApplyRequest(BaseModel):
    """The practice's answers to the preview's questions.

    ``assignments`` maps ``"<record_type>:<source_id>"`` to a contact card id
    or ``"skip"``. ``duplicates`` maps a card id to ``"create"`` or
    ``"merge:<patient id>"``. ``providers`` maps an exported provider name to
    ``"me"`` — landing another clinician's notes under that clinician is a
    later step, because they need their own grant on each client.
    ``practice`` names the proposed practice settings to take.
    """

    assignments: dict[str, str] = Field(default_factory=dict)
    duplicates: dict[str, str] = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)
    practice: dict[str, bool] = Field(default_factory=dict)


class UndoRequest(BaseModel):
    include_edited: bool = False


def _summary(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "source_system": run.source_system,
        "scope": run.scope,
        "state": run.state,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "archive_expires_at": run.archive_expires_at,
        "has_archive": run.archive_ref is not None,
        "counts": run.counts,
        "error": run.error,
    }


def _detail(run: Any) -> ImportRunDetail:
    missing = decisions_complete(run.preview, run.decisions or {}) if run.preview else []
    return ImportRunDetail(
        **_summary(run),
        preview=run.preview,
        decisions=run.decisions,
        report=run.report,
        missing=missing,
    )


def _schema(ctx: TenantContext) -> str:
    """The practice schema, which every import is scoped to and keyed by."""
    if not ctx.practice_schema:
        raise ConflictError("Imports need a practice.", code="IMPORT_NO_PRACTICE")
    return ctx.practice_schema


def _run_or_404(run_id: str) -> Any:
    run = get_run(get_db_session(), run_id)
    if run is None:
        raise NotFoundError("That import was not found.")
    return run


async def _read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise UnprocessableEntityError("That archive is too large to import.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=ImportRunDetail)
async def start_import(
    request: Request,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    scope: Annotated[Scope, Form()] = "both",
) -> ImportRunDetail:
    """Upload an export archive (a .zip of the export folder) and start its preview."""
    data = await _read_upload(file)
    base = archive_base()
    session = get_db_session()
    sweep_expired_archives(session, base, _schema(ctx), utc_now())
    try:
        ref = store_archive(base, _schema(ctx), data)
    except ArchiveError as exc:
        raise BadRequestError(str(exc), code="INVALID_ARCHIVE") from exc
    run = new_run(
        session, source_system=SOURCE_SYSTEM, scope=scope, started_by=user.id, archive_ref=ref
    )
    audit.log(
        "import_started",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"scope": scope, "bytes": len(data)},
    )
    detail = _detail(run)
    # Commit before the background step opens its own session to find the run.
    release_db_connection()
    background.add_task(run_preview, _schema(ctx), user.id, run.id)
    return detail


@router.get("/runs", response_model=ImportRunList)
def list_imports(
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunList:
    """Import history, newest first."""
    session = get_db_session()
    sweep_expired_archives(session, archive_base(), _schema(ctx), utc_now())
    runs = list_runs(session)
    audit.log(
        "import_history_viewed",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id="history",
        changes={"runs": len(runs)},
    )
    return ImportRunList(runs=[ImportRunSummary(**_summary(r)) for r in runs])


@router.get("/runs/{run_id}", response_model=ImportRunDetail)
def get_import(
    run_id: str,
    request: Request,
    _ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunDetail:
    """One run: its state, and once previewed, what it would do and what it asks."""
    run = _run_or_404(run_id)
    audit.log(
        "import_viewed",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"state": run.state},
    )
    return _detail(run)


@router.post(
    "/runs/{run_id}/apply", status_code=status.HTTP_202_ACCEPTED, response_model=ImportRunDetail
)
def apply_import(
    run_id: str,
    body: ApplyRequest,
    request: Request,
    background: BackgroundTasks,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunDetail:
    """Answer the preview's questions and land the archive."""
    run = _run_or_404(run_id)
    if run.state != "previewed":
        raise ConflictError("This import is not ready to apply.", code="IMPORT_NOT_PREVIEWED")
    if run.archive_ref is None:
        raise ConflictError(
            "The uploaded archive is no longer available. Upload it again.",
            code="IMPORT_ARCHIVE_GONE",
        )
    others = {v for v in body.providers.values() if v not in {"me", user.id}}
    if others:
        raise UnprocessableEntityError(
            "Notes can be imported under your own name only in this version.",
            code="IMPORT_PROVIDER_NOT_SUPPORTED",
        )
    decisions = body.model_dump()
    missing = decisions_complete(run.preview, decisions)
    if missing:
        raise UnprocessableEntityError(
            "Some questions still need an answer.", {"missing": missing}, code="IMPORT_UNANSWERED"
        )
    run.decisions = decisions
    run.state = "applying"
    audit.log(
        "import_applied",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={
            "assignments": len(body.assignments),
            "duplicates": len(body.duplicates),
            "practice_fields": sorted(k for k, v in body.practice.items() if v),
        },
    )
    detail = _detail(run)
    release_db_connection()
    background.add_task(run_apply, _schema(ctx), user.id, run.id, ctx.practice_id)
    return detail


@router.post("/runs/{run_id}/undo", response_model=ImportRunDetail)
def undo_import(
    run_id: str,
    body: UndoRequest,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunDetail:
    """Take a run back out. Records edited since are kept unless asked."""
    run = _run_or_404(run_id)
    if run.state != "applied":
        raise ConflictError("Only an applied import can be undone.", code="IMPORT_NOT_APPLIED")
    applier = ArchiveApplier(
        session=get_db_session(),
        settings=get_settings(),
        user_id=user.id,
        practice_id=ctx.practice_id,
        archive_dir=Path(archive_base()),
    )
    result = applier.undo(
        run_id=run.id, include_edited=body.include_edited, landed_until=run.finished_at
    )
    report = dict(run.report or {})
    report["undo"] = result
    run.report = report
    run.state = "undone"
    run.finished_at = utc_now()
    audit.log(
        "import_undone",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"removed": result["removed"], "kept": len(result["kept"])},
    )
    return _detail(run)


@router.delete("/runs/{run_id}/archive", response_model=ImportRunDetail)
def delete_import_archive(
    run_id: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunDetail:
    """Delete the uploaded archive now rather than waiting for its expiry."""
    run = _run_or_404(run_id)
    deleted = delete_archive(archive_base(), _schema(ctx), run.archive_ref)
    run.archive_ref = None
    run.archive_expires_at = None
    audit.log(
        "import_archive_deleted",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"deleted": deleted},
    )
    return _detail(run)


@router.get("/runs/{run_id}/files")
def view_import_file(
    run_id: str,
    path: str,
    request: Request,
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> FileResponse:
    """Open one file from the uploaded archive, so a record can be told apart."""
    run = _run_or_404(run_id)
    root = archive_path(archive_base(), _schema(ctx), run.archive_ref or "")
    if root is None:
        raise NotFoundError("The uploaded archive is no longer available.")
    known = {r["path"] for r in (run.preview or {}).get("records", [])}
    if path not in known:
        raise NotFoundError("That file is not part of this import.")
    target = (root / path).resolve()
    if not target.is_file() or root.resolve() not in target.parents:
        raise NotFoundError("That file is not part of this import.")
    index = sorted(known).index(path)
    audit.log(
        "import_file_viewed",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"file_index": index},
    )
    media = "application/pdf" if target.suffix.lower() == ".pdf" else "application/octet-stream"
    return FileResponse(target, media_type=media, content_disposition_type="inline")
