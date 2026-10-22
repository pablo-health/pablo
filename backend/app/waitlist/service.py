# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The waitlist's rules, over :class:`~.repository.WaitlistRepository`.

**Only an active client waits.** A client on hold or no longer seen is
refused before anything is written; the chart's status is the practice's
own answer to whether this person is being seen.

**The order is the clinician's.** A new entry goes to the end unless a
place is named; removing one closes the gap; reordering only ever permutes
the places the named entries already hold, so an entry the caller cannot see
keeps its place.

**One open entry per client per clinician.** A second add is refused rather
than quietly merged, so nobody's preferences are overwritten by accident.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from ..utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from .repository import WaitlistEntry, WaitlistRepository

WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_PARTS: tuple[str, ...] = ("morning", "afternoon", "evening")
MODALITIES: tuple[str, ...] = ("in_person", "telehealth", "either")

#: The fields a change may set on an open entry.
CHANGEABLE: frozenset[str] = frozenset(
    {"days", "times", "modality", "session_type", "paused", "no_auto_offer"}
)


class WaitlistError(Exception):
    """Base for the waitlist's refusals."""


class WaitlistNotFoundError(WaitlistError):
    """No open entry with this id on the caller's list."""


class ClientNotEligibleError(WaitlistError):
    """The client is not active, so cannot wait for a time."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class AlreadyWaitingError(WaitlistError):
    """The client already has an open entry on this clinician's list."""


class OrderMismatchError(WaitlistError):
    """A reorder named an entry that is not open on the caller's list, or named one twice."""


def _ordered(values: Sequence[str], vocabulary: tuple[str, ...]) -> list[str]:
    """``values`` de-duplicated, in the vocabulary's own order."""
    wanted = set(values)
    return [v for v in vocabulary if v in wanted]


class WaitlistService:
    def __init__(self, repo: WaitlistRepository, session: Session) -> None:
        self._repo = repo
        self._session = session

    # --- reading ---

    def list_open(self, user_id: str) -> list[WaitlistEntry]:
        return self._repo.list_open(user_id)

    def open_entry_for(self, user_id: str, patient_id: str) -> WaitlistEntry | None:
        return self._repo.open_for_patient(user_id, patient_id)

    def usual_session_type(self, patient_id: str) -> str | None:
        """The client's usual session type: that of their latest appointment."""
        return self._repo.latest_session_type(patient_id)

    def past_modalities(self, patient_id: str) -> set[str]:
        """``{'telehealth'}``, ``{'in_person'}``, both, or empty, from past visits.

        A proxy, not a record (see ``WaitlistRepository.visit_modalities``):
        use it to suggest a modality the clinician confirms.
        """
        return self._repo.visit_modalities(patient_id, utc_now())

    # --- changing ---

    def add(  # noqa: PLR0913 — one keyword per thing an entry records
        self,
        user_id: str,
        patient_id: str,
        patient_status: str,
        *,
        days: Sequence[str],
        times: Sequence[str],
        modality: str,
        session_type: str | None = None,
        position: int | None = None,
        paused: bool = False,
        no_auto_offer: bool = False,
    ) -> WaitlistEntry:
        """Put an active client on the clinician's list.

        ``session_type`` defaults to the client's usual one. ``position``
        defaults to the end; a named place moves everyone from there down one.
        """
        if patient_status != "active":
            raise ClientNotEligibleError(patient_status)
        if self._repo.open_for_patient(user_id, patient_id) is not None:
            raise AlreadyWaitingError(patient_id)
        held = self._repo.lock_open_positions(user_id)
        end = len(held) + 1
        place = end if position is None else min(max(position, 1), end)
        if place < end:
            self._repo.shift_from(user_id, place, 1)
        if session_type is None:
            session_type = self.usual_session_type(patient_id)
        try:
            with self._session.begin_nested():
                return self._repo.insert(
                    user_id=user_id,
                    patient_id=patient_id,
                    days=_ordered(days, WEEKDAYS),
                    times=_ordered(times, DAY_PARTS),
                    modality=modality,
                    session_type=session_type,
                    position=place,
                    paused=paused,
                    no_auto_offer=no_auto_offer,
                    created_by=user_id,
                    at=utc_now(),
                )
        except IntegrityError as exc:
            # Two adds for one client at once: the open-entry index let one through.
            raise AlreadyWaitingError(patient_id) from exc

    def change(self, user_id: str, entry_id: str, fields: dict[str, Any]) -> WaitlistEntry:
        """Set any of :data:`CHANGEABLE` on an open entry. Its place does not move."""
        unknown = set(fields) - CHANGEABLE
        if unknown:
            raise ValueError(f"not changeable: {sorted(unknown)}")
        values = dict(fields)
        if "days" in values:
            values["days"] = _ordered(values["days"], WEEKDAYS)
        if "times" in values:
            values["times"] = _ordered(values["times"], DAY_PARTS)
        values["updated_at"] = utc_now()
        changed = self._repo.set_values(user_id, entry_id, values)
        if changed is None:
            raise WaitlistNotFoundError(entry_id)
        return changed

    def remove(self, user_id: str, entry_id: str) -> WaitlistEntry:
        """Take an entry off the list and close the gap. Returns it as it was."""
        self._repo.lock_open_positions(user_id)
        entry = self._repo.get_open(user_id, entry_id)
        if entry is None:
            raise WaitlistNotFoundError(entry_id)
        self._repo.delete(user_id, entry_id)
        self._repo.shift_from(user_id, entry.position + 1, -1)
        return entry

    def reorder(self, user_id: str, entry_ids: Sequence[str]) -> list[WaitlistEntry]:
        """Put the named entries in this order, in the places they already hold.

        Naming every entry on the list is the ordinary case and reorders the
        whole list; naming some of them reorders just those among themselves.
        """
        held = dict(self._repo.lock_open_positions(user_id))
        if len(set(entry_ids)) != len(entry_ids) or any(i not in held for i in entry_ids):
            raise OrderMismatchError(user_id)
        places = sorted(held[i] for i in entry_ids)
        for entry_id, place in zip(entry_ids, places, strict=True):
            if held[entry_id] != place:
                self._repo.set_position(user_id, entry_id, place)
        return self._repo.list_open(user_id)
