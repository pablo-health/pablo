# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The off-request halves of an import: build the preview, land the archive.

Each function opens its own tenant-armed session, does one step, records the
outcome on the run row and never raises: a failed step leaves the run in
``failed`` with a short, content-free reason the screen can show. A reader
error names the file, never what was in it.

These run on whatever the deployment gives off-request work — FastAPI
background tasks on a single-instance stack, a task queue elsewhere. They take
only ids, so either runner can call them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..chart_proposals.step import ChartProposalStep
from ..db.models import ImportRecordRow
from ..db.tenant_session import tenant_db_session
from ..repositories import (
    get_chart_history_repository,
    get_chart_proposal_repository,
    get_medication_repository,
    get_notes_repository,
    get_patient_repository,
    get_user_repository,
)
from ..services.note_generation_factory import note_generation_service
from ..services.note_service import NoteService
from ..settings import get_settings
from ..utcnow import utc_now
from .apply import ArchiveApplier, edited_since
from .archive_store import ArchiveStore, archive_store_from_settings
from .chart_seed import ChartSeeder, SeedReport
from .ledger import get_run, ledger_snapshot
from .preview import Clock, ExistingPatient, PreviewInputs, build_calendar_preview, build_preview
from .readers import detect_layout, read_simplepractice_calendar
from .readers.simplepractice import SOURCE_SYSTEM, read_simplepractice_archive

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Page size when listing the practice's patients for duplicate matching.
_PATIENT_PAGE = 200


def archive_base() -> Path:
    return Path(get_settings().import_archive_dir)


def archive_store() -> ArchiveStore:
    """Where uploaded exports are kept, as the deployment configured it."""
    return archive_store_from_settings(get_settings())


def _existing_patients(user_id: str) -> list[ExistingPatient]:
    repo = get_patient_repository()
    out: list[ExistingPatient] = []
    page = 1
    while True:
        patients, total = repo.list_by_user(user_id, page=page, page_size=_PATIENT_PAGE)
        out.extend(ExistingPatient.from_patient(p) for p in patients)
        if page * _PATIENT_PAGE >= total or not patients:
            return out
        page += 1


def _edited_targets(session: Session) -> set[tuple[str, str]]:
    rows = session.execute(
        select(ImportRecordRow).where(
            ImportRecordRow.source_system == SOURCE_SYSTEM, ImportRecordRow.state != "undone"
        )
    ).scalars()
    return {
        (r.target_table, r.target_id)
        for r in rows
        if edited_since(session, r.target_table, r.target_id, r.updated_at)
    }


def _clock(user_id: str) -> Clock:
    """Now, in the zone a calendar feed's "today" is judged in: the clinician's own."""
    return Clock(utc_now(), ZoneInfo(get_user_repository().get_preferences(user_id).timezone))


def _fail(run: Any, reason: str) -> None:
    run.state = "failed"
    run.error = reason
    run.finished_at = utc_now()


def run_preview(schema: str, user_id: str, run_id: str) -> None:
    """Read the uploaded archive and store what it would become."""
    with tenant_db_session(schema, user_id) as session:
        run = get_run(session, run_id)
        if run is None:
            return
        run.state = "previewing"
        session.flush()
        path = archive_store().path(schema, run.archive_ref or "")
        if path is None:
            _fail(run, "The uploaded archive is no longer available. Upload it again.")
            return
        try:
            inputs = PreviewInputs(
                ledger=ledger_snapshot(session, SOURCE_SYSTEM),
                existing_patients=_existing_patients(user_id),
                edited_targets=_edited_targets(session),
                scope=run.scope,
            )
            if detect_layout(path) == "calendar":
                calendar = read_simplepractice_calendar(path)
                run.preview = build_calendar_preview(calendar, inputs, clock=_clock(user_id))
            else:
                archive = read_simplepractice_archive(path)
                if not archive.contacts and not archive.notes:
                    _fail(run, "This does not look like a SimplePractice export.")
                    return
                run.preview = build_preview(archive, inputs)
            run.counts = run.preview["counts"]
            run.state = "previewed"
        except Exception:  # a preview must end in a state, whatever the archive held
            logger.exception("import preview failed", extra={"import_run_id": run_id})
            _fail(run, "The archive could not be read.")


def run_apply(schema: str, user_id: str, run_id: str, practice_id: str | None) -> None:
    """Land a previewed archive with the decisions stored on the run."""
    with tenant_db_session(schema, user_id) as session:
        run = get_run(session, run_id)
        if run is None or run.preview is None:
            return
        store = archive_store()
        path = store.path(schema, run.archive_ref or "")
        if path is None:
            _fail(run, "The uploaded archive is no longer available. Upload it again.")
            return
        try:
            applier = ArchiveApplier(
                session=session,
                settings=get_settings(),
                user_id=user_id,
                practice_id=practice_id,
                archive_dir=path,
            )
            calendar_layout = detect_layout(path) == "calendar"
            if calendar_layout:
                report = applier.apply_calendar(
                    run_id=run.id,
                    calendar=read_simplepractice_calendar(path),
                    preview=run.preview,
                    decisions=run.decisions or {},
                    clock=_clock(user_id),
                )
            else:
                report = applier.apply(
                    run_id=run.id,
                    archive=read_simplepractice_archive(path),
                    preview=run.preview,
                    decisions=run.decisions or {},
                )
            run.report = report.as_dict()
            run.counts = run.report["counts"]
            run.state = "applied"
            run.finished_at = utc_now()
        except Exception:  # apply must end in a state; the transaction rolls back
            logger.exception("import apply failed", extra={"import_run_id": run_id})
            session.rollback()
            run = get_run(session, run_id)
            if run is not None:
                _fail(run, "The import stopped part way and nothing was kept. Try again.")
            return
        # The archive has done its job; it is not kept past apply.
        store.delete(schema, run.archive_ref)
        run.archive_ref = None
        run.archive_expires_at = None
        landed = sorted(set(report.patients.values()))
    if calendar_layout:
        return  # a calendar feed carries no notes, so there is nothing to propose from
    # The import is applied and committed. What its notes say about each
    # client's chart is proposed afterwards, one client per transaction, so
    # a model call never holds the import open and a failure costs nothing
    # already landed.
    seeded = _seed_charts(schema, user_id, run_id, landed)
    with tenant_db_session(schema, user_id) as session:
        run = get_run(session, run_id)
        if run is not None:
            run.report = {**(run.report or {}), "chart_updates": seeded.as_dict()}


def _seed_charts(schema: str, user_id: str, run_id: str, patient_ids: list[str]) -> SeedReport:
    """Propose each landed client's chart from their imported notes. Never raises."""
    report = SeedReport()
    try:
        # The same model the app drafts with: the end-to-end stack's stand-in
        # when one is configured, else the real one.
        generator = note_generation_service()
    except Exception:
        logger.exception("chart seeding skipped: no model to propose with")
        return report
    for patient_id in patient_ids:
        try:
            with tenant_db_session(schema, user_id):
                patient = get_patient_repository().get(patient_id, user_id)
                if patient is None:
                    continue
                step = ChartProposalStep(
                    get_chart_proposal_repository(),
                    get_chart_history_repository(),
                    get_medication_repository(),
                )
                notes = NoteService(get_notes_repository()).list_notes_for_patient(
                    patient_id, user_id
                )
                ChartSeeder(user_id, step, generator).seed(patient, notes, report)
        except Exception:  # one client's failure never costs the others theirs
            # The run, not the client: a client id is a patient identifier.
            logger.exception("chart seeding failed for a client", extra={"import_run_id": run_id})
            report.failed += 1
    return report


__all__ = ["archive_base", "run_apply", "run_preview"]
