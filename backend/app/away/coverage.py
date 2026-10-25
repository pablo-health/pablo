# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Coverage: a colleague looks after a clinician's clients while the clinician is away.

**Nothing before acceptance.** Naming a colleague on an away period asks them
to cover; it grants nothing. When they accept, every client the away
clinician holds as ``primary`` gets a ``covering`` grant for the colleague.
When they decline, the decline is all that is written.

**Exactly the away dates.** A grant starts at midnight on the first day away
and expires at midnight on the day the clinician is back, both on the away
clinician's own calendar — the frame they chose the dates in.
``has_patient_access`` honours ``starts_at`` as well as ``expires_at``, so
coverage accepted weeks ahead grants nothing early, and nothing has to run at
the return for access to end.

**Ending early ends it now.** Ending the period, changing its dates or its
colleague, or the colleague withdrawing, sets every one of its grants'
``expires_at`` to now. Changing the dates or the colleague also puts the
request back to ``requested``: the colleague accepted particular dates, and
nobody covers dates they did not accept.

**Clients added meanwhile.** A client whose chart is created for the away
clinician while accepted coverage is ahead or under way gets the same grant
(:func:`extend_coverage_to_new_client`).

**Written as the colleague.** A ``patient_clinicians`` row is visible to, and
writable by, only the clinician it names, so the away clinician's clients are
read as the away clinician (:func:`app.db.read_once_as`) and the colleague's
grants are written as the colleague (:func:`_as_clinician`). Who may cause
each write is decided here and in the routes: the colleague accepts, and only
the period's own clinician (or the colleague withdrawing) ends it. Nothing
else uses either.

**One coverage per colleague per clinician at a time.** A grant is one row
per client per clinician, so a colleague cannot hold two windows on the same
client. Accepting a second coverage of the same clinician while an earlier
one is still ahead or under way is refused (:class:`CoverageConflict`).

**Audited.** Acceptance, decline and each client's grant (with its window)
and early end are audit rows. Every other audit row the colleague writes on a
client while a covering grant is in force is tagged with the grant's role and
period (``app.repositories.postgres.audit``), which is what
:func:`coverage_activity` reads back for the returning clinician through the
``rls_audit_coverage_read`` policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from ..db import read_once_as
from ..db.models import AuditLogRow, PatientClinicianRow, PatientRow
from ..models.enums import ClinicianRole
from ..repositories.postgres._grants import grant_in_force
from ..repositories.postgres.audit import COVERAGE_PERIOD_KEY
from .repository import (
    COVERAGE_ACCEPTED,
    COVERAGE_DECLINED,
    AwayPeriod,
    AwayPeriodRepository,
)
from .service import clinician_today, clinician_zone

if TYPE_CHECKING:
    from sqlalchemy import Executable
    from sqlalchemy.orm import Session

#: How many rows of a colleague's activity one read returns, newest first.
ACTIVITY_LIMIT = 500


class CoverageConflict(Exception):  # noqa: N818 — a refusal the route turns into a 409
    """The colleague already covers this clinician for other dates still ahead or under way."""


@dataclass(frozen=True)
class CoverageWindow:
    starts_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CoverageActivity:
    """One thing the covering colleague did on a client, from the audit log."""

    at: datetime
    action: str
    resource_type: str
    resource_id: str
    patient_id: str
    client_first_name: str | None
    client_last_name: str | None


def coverage_window(session: Session, period: AwayPeriod) -> CoverageWindow:
    """Midnight on the first day away until midnight on the day back.

    Both on the away clinician's own calendar.
    """
    zone = clinician_zone(session, period.user_id)
    return CoverageWindow(
        starts_at=datetime.combine(period.start_date, time(0), zone),
        expires_at=datetime.combine(period.return_date, time(0), zone),
    )


def accept_coverage(session: Session, period: AwayPeriod, now: datetime) -> list[str]:
    """The named colleague accepts: record it and grant them the clinician's clients.

    The caller is the colleague, and has checked the period names them and is
    neither ended nor past. Returns the clients granted.
    """
    cover = _cover(period)
    repo = AwayPeriodRepository(session)
    today = clinician_today(session, period.user_id, now)
    if any(
        other.id != period.id and other.covering_user_id == cover
        for other in repo.accepted_ahead(period.user_id, today)
    ):
        raise CoverageConflict
    repo.set_coverage(period.id, cover, COVERAGE_ACCEPTED, now)
    return _grant(session, period, _primary_clients(session, period.user_id), now)


def decline_coverage(session: Session, period: AwayPeriod, now: datetime) -> list[str]:
    """The named colleague declines, or withdraws an acceptance.

    Returns the clients whose access that ended.
    """
    ended = end_coverage(session, period, now)
    AwayPeriodRepository(session).set_coverage(period.id, _cover(period), COVERAGE_DECLINED, now)
    return ended


def end_coverage(session: Session, period: AwayPeriod, now: datetime) -> list[str]:
    """End every grant of this period's that has not already expired. Returns those clients."""
    if period.covering_user_id is None:
        return []
    statement = (
        update(PatientClinicianRow)
        .where(
            PatientClinicianRow.user_id == period.covering_user_id,
            PatientClinicianRow.role == ClinicianRole.COVERING.value,
            PatientClinicianRow.away_period_id == period.id,
            or_(PatientClinicianRow.expires_at.is_(None), PatientClinicianRow.expires_at > now),
        )
        .values(expires_at=now)
        .returning(PatientClinicianRow.patient_id)
    )
    return [str(row[0]) for row in _as_clinician(session, period.covering_user_id, statement)]


def extend_coverage_to_new_client(
    session: Session, clinician_id: str, patient_id: str, now: datetime
) -> list[AwayPeriod]:
    """Give a new client of *clinician_id* the grant each accepted coverage ahead carries.

    Called on the clinician's own session as their chart for the client is
    created, below any route, so it writes its own audit row: the clinician
    is the actor, as they are of the chart's creation. Returns the periods
    whose colleague was granted the client.
    """
    from ..models.audit import AuditAction  # noqa: PLC0415 — cycle
    from ..repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
    from ..services.audit_service import AuditService  # noqa: PLC0415

    today = clinician_today(session, clinician_id, now)
    extended = []
    for period in AwayPeriodRepository(session).accepted_ahead(clinician_id, today):
        if _grant(session, period, [patient_id], now):
            extended.append(period)
            AuditService(PostgresAuditRepository(session)).log_coverage_action(
                AuditAction.COVERAGE_ACCESS_GRANTED,
                actor_id=clinician_id,
                request=None,
                away_period_id=period.id,
                patient_ids=[patient_id],
                changes=window_changes(session, period),
            )
    return extended


def window_changes(session: Session, period: AwayPeriod) -> dict[str, str]:
    """The grant's window, for an audit row's ``changes``."""
    window = coverage_window(session, period)
    return {
        "starts_at": window.starts_at.isoformat(),
        "expires_at": window.expires_at.isoformat(),
    }


def coverage_activity(session: Session, period: AwayPeriod) -> list[CoverageActivity]:
    """What the colleague did on the clinician's clients under this period's coverage, newest first.

    Read on the away clinician's own session: ``rls_audit_coverage_read`` is
    what lets them see rows another clinician wrote, and only these.
    """
    if period.covering_user_id is None:
        return []
    rows = session.execute(
        select(
            AuditLogRow.timestamp,
            AuditLogRow.action,
            AuditLogRow.resource_type,
            AuditLogRow.resource_id,
            AuditLogRow.patient_id,
            PatientRow.first_name,
            PatientRow.last_name,
        )
        .outerjoin(PatientRow, PatientRow.id == AuditLogRow.patient_id)
        .where(
            AuditLogRow.user_id == period.covering_user_id,
            AuditLogRow.patient_id.is_not(None),
            AuditLogRow.changes[COVERAGE_PERIOD_KEY].astext == period.id,
        )
        .order_by(AuditLogRow.timestamp.desc(), AuditLogRow.id.desc())
        .limit(ACTIVITY_LIMIT)
    ).all()
    return [
        CoverageActivity(
            at=row.timestamp,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            patient_id=str(row.patient_id),
            client_first_name=row.first_name,
            client_last_name=row.last_name,
        )
        for row in rows
    ]


def _cover(period: AwayPeriod) -> str:
    if period.covering_user_id is None:
        msg = "coverage: the period names nobody to cover"
        raise ValueError(msg)
    return period.covering_user_id


def _primary_clients(session: Session, clinician_id: str) -> list[str]:
    """Every live chart *clinician_id* holds as primary, read as that clinician."""
    rows = read_once_as(
        session,
        principal="app.current_user_id",
        value=clinician_id,
        statement=select(PatientClinicianRow.patient_id)
        .join(PatientRow, PatientRow.id == PatientClinicianRow.patient_id)
        .where(
            PatientClinicianRow.user_id == clinician_id,
            PatientClinicianRow.role == ClinicianRole.PRIMARY.value,
            grant_in_force(),
            PatientRow.deleted_at.is_(None),
            PatientRow.status != "pending",
        ),
    )
    return [str(row[0]) for row in rows]


def _grant(
    session: Session, period: AwayPeriod, patient_ids: list[str], now: datetime
) -> list[str]:
    """Write the period's covering grant on each client. Returns the clients granted.

    A client the colleague already reaches some other way (a primary,
    co-treating or supervising grant of their own) keeps that grant and is
    not granted here. A covering row left from an earlier, finished coverage
    is reused for this one.
    """
    if not patient_ids:
        return []
    cover = _cover(period)
    window = coverage_window(session, period)
    values = [
        {
            "patient_id": patient_id,
            "user_id": cover,
            "role": ClinicianRole.COVERING.value,
            "granted_at": now,
            "granted_by": period.user_id,
            "starts_at": window.starts_at,
            "expires_at": window.expires_at,
            "away_period_id": period.id,
        }
        for patient_id in patient_ids
    ]
    proposed = insert(PatientClinicianRow).values(values)
    existing = PatientClinicianRow.__table__.c
    statement = proposed.on_conflict_do_update(
        index_elements=["patient_id", "user_id"],
        set_={
            "granted_at": proposed.excluded.granted_at,
            "granted_by": proposed.excluded.granted_by,
            "starts_at": proposed.excluded.starts_at,
            "expires_at": proposed.excluded.expires_at,
            "away_period_id": proposed.excluded.away_period_id,
        },
        where=(existing.role == ClinicianRole.COVERING.value)
        & ((existing.away_period_id == period.id) | (existing.expires_at <= now)),
    ).returning(PatientClinicianRow.patient_id)
    return [str(row[0]) for row in _as_clinician(session, cover, statement)]


def _as_clinician(session: Session, user_id: str, statement: Executable) -> list[tuple[str]]:
    """Run one write as *user_id*, then put the caller's identity back.

    The setting is transaction-local and made inside a savepoint: if the
    statement fails, rolling the savepoint back reverts it; if it succeeds,
    the caller's value is restored before the savepoint is released.
    Nothing touches ``session.info`` or a ContextVar, so no listener re-arms
    the borrowed identity later.
    """
    savepoint = session.begin_nested()
    try:
        previous = session.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        ).scalar()
        session.execute(
            text("SELECT set_config('app.current_user_id', :value, true)"), {"value": user_id}
        )
        rows = [(row[0],) for row in session.execute(statement).all()]
        session.execute(
            text("SELECT set_config('app.current_user_id', :value, true)"),
            {"value": previous or ""},
        )
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()
    return rows
