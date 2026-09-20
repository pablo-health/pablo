# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The room Pablo has never heard of.

A clinician pastes a URL and that is the appointment's room. It covers Teams,
VSee, a practice's own Jitsi, a phone bridge — everything a fixed list of
adapters cannot, which over a long enough period is most things.

It makes no link of its own, which is why :meth:`create_for_appointment`
answers None. By the time this provider is reached the pasted URL has already
won outright in ``provision_meeting``; this exists so that "the clinician
supplies it" is a provider with a name rather than an absence, and so the
cancel path has somebody to ask who correctly answers "nothing to release".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..services.telehealth import MANUAL

if TYPE_CHECKING:
    from ..scheduling_engine.models.appointment import Appointment
    from ..services.telehealth import Attendee, Clinician, MeetingLink, Practice


class ManualProvider:
    """A URL a person typed."""

    provider_id: ClassVar[str] = MANUAL
    display_name: ClassVar[str] = "A link I'll paste in"

    def is_connected(self, _clinician: Clinician) -> bool:
        """Always. There is nothing to connect."""
        return True

    def create_for_appointment(
        self,
        _appointment: Appointment,
        _clinician: Clinician,
        _practice: Practice,
        _attendee: Attendee,
    ) -> MeetingLink | None:
        return None

    def cancel(self, _external_id: str, _clinician: Clinician) -> bool:
        """Nothing was issued, so nothing is released."""
        return False
