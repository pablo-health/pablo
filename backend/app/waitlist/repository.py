# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Waitlist entries, read and written on a tenant-scoped clinician session.

Every query names the clinician (``user_id``), and the row policy on
``waitlist_entries`` tests the same column underneath, so a clinician only
ever reads and orders their own list. Names come from a join to
``patients``, whose own policy decides which clients this clinician may see:
an entry for a client they no longer hold a grant on drops out of the list.

Positions are dense from 1 among the clinician's open entries. Every change
to them locks those rows first, so two changes at once cannot leave two
entries in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from ..db.models import AppointmentRow, PatientRow, WaitlistEntryRow

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class WaitlistEntry:
    id: str
    user_id: str
    patient_id: str
    days: tuple[str, ...]
    times: tuple[str, ...]
    modality: str
    session_type: str | None
    position: int
    paused: bool
    no_auto_offer: bool
    filled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    #: First and last name, as the chart has them; ``None`` when the client
    #: is not visible to this clinician.
    patient_name: str | None = None


def _entry(row: WaitlistEntryRow, patient_name: str | None = None) -> WaitlistEntry:
    return WaitlistEntry(
        id=row.id,
        user_id=row.user_id,
        patient_id=row.patient_id,
        days=tuple(row.days),
        times=tuple(row.times),
        modality=row.modality,
        session_type=row.session_type,
        position=row.position,
        paused=row.paused,
        no_auto_offer=row.no_auto_offer,
        filled_at=row.filled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        patient_name=patient_name,
    )


def _name(first: str | None, last: str | None) -> str:
    return f"{first or ''} {last or ''}".strip()


class WaitlistRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_open(self, user_id: str) -> list[WaitlistEntry]:
        """The clinician's open entries, in their order, with each client's name."""
        rows = self._session.execute(
            select(WaitlistEntryRow, PatientRow.first_name, PatientRow.last_name)
            .join(PatientRow, PatientRow.id == WaitlistEntryRow.patient_id)
            .where(WaitlistEntryRow.user_id == user_id, WaitlistEntryRow.filled_at.is_(None))
            .order_by(WaitlistEntryRow.position, WaitlistEntryRow.created_at)
        ).all()
        return [_entry(row, _name(first, last)) for row, first, last in rows]

    def get_open(self, user_id: str, entry_id: str) -> WaitlistEntry | None:
        row = self._session.scalar(
            select(WaitlistEntryRow).where(
                WaitlistEntryRow.id == entry_id,
                WaitlistEntryRow.user_id == user_id,
                WaitlistEntryRow.filled_at.is_(None),
            )
        )
        return None if row is None else _entry(row)

    def open_for_patient(self, user_id: str, patient_id: str) -> WaitlistEntry | None:
        row = self._session.scalar(
            select(WaitlistEntryRow).where(
                WaitlistEntryRow.user_id == user_id,
                WaitlistEntryRow.patient_id == patient_id,
                WaitlistEntryRow.filled_at.is_(None),
            )
        )
        return None if row is None else _entry(row)

    def lock_open_positions(self, user_id: str) -> list[tuple[str, int]]:
        """Lock the clinician's open entries and return ``(id, position)`` in order."""
        rows = self._session.execute(
            select(WaitlistEntryRow.id, WaitlistEntryRow.position)
            .where(WaitlistEntryRow.user_id == user_id, WaitlistEntryRow.filled_at.is_(None))
            .order_by(WaitlistEntryRow.position, WaitlistEntryRow.created_at)
            .with_for_update()
        ).all()
        return [(r[0], r[1]) for r in rows]

    def shift_from(self, user_id: str, position: int, by: int) -> None:
        """Move every open entry at ``position`` or later by ``by`` places."""
        self._session.execute(
            update(WaitlistEntryRow)
            .where(
                WaitlistEntryRow.user_id == user_id,
                WaitlistEntryRow.filled_at.is_(None),
                WaitlistEntryRow.position >= position,
            )
            .values(position=WaitlistEntryRow.position + by)
        )

    def insert(  # noqa: PLR0913 — one keyword per thing an entry records
        self,
        *,
        user_id: str,
        patient_id: str,
        days: list[str],
        times: list[str],
        modality: str,
        session_type: str | None,
        position: int,
        paused: bool,
        no_auto_offer: bool,
        created_by: str,
        at: datetime,
    ) -> WaitlistEntry:
        row = WaitlistEntryRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient_id,
            days=days,
            times=times,
            modality=modality,
            session_type=session_type,
            position=position,
            paused=paused,
            no_auto_offer=no_auto_offer,
            created_by=created_by,
            created_at=at,
            updated_at=at,
        )
        self._session.add(row)
        self._session.flush()
        return _entry(row)

    def set_values(
        self, user_id: str, entry_id: str, values: dict[str, Any]
    ) -> WaitlistEntry | None:
        row = self._session.execute(
            update(WaitlistEntryRow)
            .where(
                WaitlistEntryRow.id == entry_id,
                WaitlistEntryRow.user_id == user_id,
                WaitlistEntryRow.filled_at.is_(None),
            )
            .values(**values)
            .returning(WaitlistEntryRow)
        ).scalar()
        return None if row is None else _entry(row)

    def set_position(self, user_id: str, entry_id: str, position: int) -> None:
        self._session.execute(
            update(WaitlistEntryRow)
            .where(WaitlistEntryRow.id == entry_id, WaitlistEntryRow.user_id == user_id)
            .values(position=position)
        )

    def delete(self, user_id: str, entry_id: str) -> None:
        self._session.query(WaitlistEntryRow).filter(
            WaitlistEntryRow.id == entry_id, WaitlistEntryRow.user_id == user_id
        ).delete(synchronize_session=False)

    # --- what the client's own appointments say ---

    def latest_session_type(self, patient_id: str) -> str | None:
        """The session type of the client's most recent appointment that was not cancelled."""
        return self._session.scalar(
            select(AppointmentRow.session_type)
            .where(AppointmentRow.patient_id == patient_id, AppointmentRow.status != "cancelled")
            .order_by(AppointmentRow.start_at.desc())
            .limit(1)
        )

    def visit_modalities(self, patient_id: str, before: datetime) -> set[str]:
        """Whether the client's past visits were by video, in person, or both.

        There is no modality column on an appointment, so a video link stands
        in for one: a visit with a link was by video, one without was in
        person. It is a proxy — a video visit held on a link kept elsewhere
        carries none — so callers offer it as a starting point to confirm,
        never as a decision.
        """
        has_link = AppointmentRow.video_link.is_not(None) & (
            func.length(func.trim(AppointmentRow.video_link)) > 0
        )
        rows = self._session.execute(
            select(has_link)
            .where(
                AppointmentRow.patient_id == patient_id,
                AppointmentRow.status != "cancelled",
                AppointmentRow.start_at < before,
            )
            .distinct()
        ).all()
        return {"telehealth" if r[0] else "in_person" for r in rows}
