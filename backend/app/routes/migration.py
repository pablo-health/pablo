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
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..db import arm_current_user_id, get_db_session, release_db_connection, set_tenant_schema
from ..jobs.task_queue import enqueue
from ..migration.apply import ArchiveApplier
from ..migration.jobs import archive_base, archive_store, run_apply, run_preview
from ..migration.ledger import (
    ArchiveError,
    RecordState,
    archive_file,
    client_records_for_run,
    get_run,
    list_runs,
    new_run,
    record_state,
    sweep_expired_archives,
)
from ..migration.preview import decisions_complete
from ..migration.readers import MIXED_LAYOUT_REASON, NO_LAYOUT_REASON, detect_layout
from ..migration.readers.simplepractice import SOURCE_SYSTEM
from ..models import User
from ..models.audit import ResourceType
from ..repositories import PatientRepository, UserRepository, get_user_repository
from ..services import AuditService, get_audit_service
from ..services.session_generation_worker import resolve_tenant_for_user
from ..settings import get_settings
from ..utcnow import utc_now
from .patients import get_patient_repository

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


class ImportRunClient(BaseModel):
    """A client this run brought in, who is still in the chart.

    ``state`` is ``landed`` for a client the run created, ``updated`` for one
    it changed, ``merged`` for one who was already here and had the run's
    records added to them.
    """

    patient_id: str
    first_name: str
    last_name: str
    state: RecordState


class ImportRunDetail(ImportRunSummary):
    preview: dict[str, Any] | None
    decisions: dict[str, Any] | None
    report: dict[str, Any] | None
    missing: list[str] = Field(default_factory=list)
    #: Filled in when one run is read; empty on the responses that start,
    #: apply or undo a run, which the screen follows with a read.
    clients: list[ImportRunClient] = Field(default_factory=list)


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


def _detail(run: Any, clients: list[ImportRunClient] | None = None) -> ImportRunDetail:
    missing = decisions_complete(run.preview, run.decisions or {}) if run.preview else []
    return ImportRunDetail(
        **_summary(run),
        preview=run.preview,
        decisions=run.decisions,
        report=run.report,
        missing=missing,
        clients=clients or [],
    )


def _clients(run: Any, patients: PatientRepository, user_id: str) -> list[ImportRunClient]:
    """The run's clients who are still in the chart, as this clinician may see them.

    The ledger names every patient the run landed on; the repository decides
    which of them this clinician can still open (the grant, the row policy,
    and whether the chart has since been deleted). One the repository leaves
    out is simply not listed — no error, no placeholder.
    """
    records = client_records_for_run(get_db_session(), run.id)
    found = patients.get_multiple([r.target_id for r in records], user_id)
    clients = [
        ImportRunClient(
            patient_id=patient.id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            state=record_state(record),
        )
        for record in records
        if (patient := found.get(record.target_id)) is not None
    ]
    clients.sort(key=lambda c: (c.last_name.casefold(), c.first_name.casefold()))
    return clients


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


def _one_layout(unpacked: Path) -> None:
    """Refuse an upload no reader takes, before the store copies it anywhere:
    one holding both layouts, or one holding neither."""
    layout = detect_layout(unpacked)
    if layout == "mixed":
        raise ArchiveError(MIXED_LAYOUT_REASON, code="INVALID_ARCHIVE")
    if layout is None:
        raise ArchiveError(NO_LAYOUT_REASON, code="INVALID_ARCHIVE")


async def _spool_upload(file: UploadFile, into: Path) -> int:
    """Write the upload to ``into`` a chunk at a time and return its size.

    An export can be most of a gigabyte; it is never held in memory whole.
    """
    total = 0
    with into.open("wb") as out:
        while chunk := await file.read(_READ_CHUNK):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise UnprocessableEntityError("That archive is too large to import.")
            out.write(chunk)
    return total


# --------------------------------------------------------------- the two steps
#
# Preview and apply read every file in the export and can run for minutes, so
# they run off the request: on Cloud Tasks where a queue is configured, as a
# request background task in development or where there is none. Either way
# the step takes only ids and finds its run through its own session, and the
# archive comes from the store, which any instance can read.


class ImportStepJob(BaseModel):
    """A Cloud Tasks payload: which run to work on, as whom.

    The tenant is resolved from ``user_id`` by the worker, never read from
    the payload: the caller is the Cloud Tasks service account, and the
    user's own tenant is the only one their run can be in.
    """

    user_id: str
    run_id: str


def _dispatch(
    background: BackgroundTasks,
    step: Literal["preview", "apply"],
    ctx: TenantContext,
    user_id: str,
    run_id: str,
) -> None:
    settings = get_settings()
    if settings.is_development:
        if step == "preview":
            background.add_task(run_preview, _schema(ctx), user_id, run_id)
        else:
            background.add_task(run_apply, _schema(ctx), user_id, run_id, ctx.practice_id)
        return
    enqueue(
        settings.import_task_queue,
        f"/api/internal/jobs/import-{step}",
        ImportStepJob(user_id=user_id, run_id=run_id).model_dump(),
        dedup_key=f"import-{step}-{run_id}",
    )


internal_jobs_router = APIRouter(tags=["migration"])


def _run_step(
    step: Literal["preview", "apply"], payload: ImportStepJob, user_repo: UserRepository
) -> User | None:
    """Scope the worker to the job's tenant and run the step; the owner comes
    back for the route to audit against, ``None`` when there is no tenant.

    Invoked only by Cloud Tasks (service-account OIDC, enforced by
    ``require_cloud_tasks_invoker``). A ``user_id`` that resolves to no active
    tenant is dropped as non-retryable; otherwise the step never raises and
    always leaves the run in a state. A retry re-reads the archive (preview)
    or finds nothing left to land (apply).
    """
    resolved = resolve_tenant_for_user(payload.user_id)
    if resolved is None:
        logger.warning("import-%s job: no active tenant for the user; dropping", step)
        return None
    practice_id, schema = resolved
    if step == "preview":
        run_preview(schema, payload.user_id, payload.run_id)
    else:
        run_apply(schema, payload.user_id, payload.run_id, practice_id)
    # The step wrote through its own session; the audit entry goes through the
    # request's, pointed at the same tenant and armed for the same user.
    session = get_db_session()
    set_tenant_schema(session, schema)
    arm_current_user_id(session, payload.user_id)
    owner = user_repo.get(payload.user_id)
    if owner is None:
        logger.warning("import-%s job: owner not found for audit on run %s", step, payload.run_id)
    return owner


@internal_jobs_router.post("/api/internal/jobs/import-preview", status_code=status.HTTP_200_OK)
def import_preview_job(
    payload: ImportStepJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: read the run's archive and store what it would become."""
    owner = _run_step("preview", payload, user_repo)
    if owner is None:
        return {"status": "unknown_tenant"}
    audit.log(
        "import_preview_ran",
        owner,
        http_request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=payload.run_id,
        changes={"runner": "cloud_tasks"},
    )
    return {"status": "done"}


@internal_jobs_router.post("/api/internal/jobs/import-apply", status_code=status.HTTP_200_OK)
def import_apply_job(
    payload: ImportStepJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: land the run with the decisions stored on it."""
    owner = _run_step("apply", payload, user_repo)
    if owner is None:
        return {"status": "unknown_tenant"}
    audit.log(
        "import_apply_ran",
        owner,
        http_request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=payload.run_id,
        changes={"runner": "cloud_tasks"},
    )
    return {"status": "done"}


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED, response_model=ImportRunDetail)
async def start_import(
    request: Request,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    ctx: Annotated[TenantContext, Depends(get_tenant_context)],
    user: Annotated[User, Depends(require_baa_acceptance)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    scope: Annotated[Scope, Form()] = "both",
    password: Annotated[str | None, Form()] = None,
) -> ImportRunDetail:
    """Upload an export archive and start its preview.

    The zip is either the export folder or a ``calendar/`` folder of saved
    web-calendar responses; the preview reads whichever it holds, and a zip
    holding both is refused here.

    ``password`` opens a protected export (SimplePractice protects every one).
    It is used to unpack the archive and kept nowhere: not on the run, not in
    the audit trail, not in a log.
    """
    store = archive_store()
    session = get_db_session()
    sweep_expired_archives(session, store, _schema(ctx), utc_now())
    with tempfile.TemporaryDirectory(prefix="pablo-import-upload-") as spool:
        upload = Path(spool) / "export.zip"
        size = await _spool_upload(file, upload)
        try:
            ref = store.put(_schema(ctx), upload, password or None, check=_one_layout)
        except ArchiveError as exc:
            raise BadRequestError(str(exc), code=exc.code) from exc
    run = new_run(
        session, source_system=SOURCE_SYSTEM, scope=scope, started_by=user.id, archive_ref=ref
    )
    audit.log(
        "import_started",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"scope": scope, "bytes": size},
    )
    detail = _detail(run)
    # Commit before the step opens its own session to find the run.
    release_db_connection()
    _dispatch(background, "preview", ctx, user.id, run.id)
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
    sweep_expired_archives(session, archive_store(), _schema(ctx), utc_now())
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
    patients: Annotated[PatientRepository, Depends(get_patient_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ImportRunDetail:
    """One run: its state, and once previewed, what it would do and what it asks.

    Once applied, also the clients it brought in who are still in the chart.
    """
    run = _run_or_404(run_id)
    clients = _clients(run, patients, user.id)
    audit.log(
        "import_viewed",
        user,
        request,
        resource_type=ResourceType.IMPORT_RUN,
        resource_id=run.id,
        changes={"state": run.state, "clients": len(clients)},
    )
    return _detail(run, clients)


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
    _dispatch(background, "apply", ctx, user.id, run.id)
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
        changes={
            "removed": result["removed"],
            "restored": result.get("restored", {}),
            "kept": len(result["kept"]),
        },
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
    deleted = archive_store().delete(_schema(ctx), run.archive_ref)
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
    root = archive_store().path(_schema(ctx), run.archive_ref or "")
    if root is None:
        raise NotFoundError("The uploaded archive is no longer available.")
    known = {r["path"] for r in (run.preview or {}).get("records", [])}
    if path not in known:
        raise NotFoundError("That file is not part of this import.")
    target = archive_file(root, path)
    if target is None:
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
