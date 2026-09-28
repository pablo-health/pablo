# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Put one client's invitation together, for sending or for looking at.

The preview a clinician reads before pressing Send and the email the invite
route actually sends are both built here, from the same template and the same
facts, so the preview is the email with only the link withheld.

The facts are the client's first name, their primary clinician's name, the
practice's name, the forms still waiting for them, and how long the link
works. The forms are the ones the
client has been asked for and can still write to, plus — for a preview taken
before anything is sent — the ones about to be asked for.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from ..auth.service import TenantContext, get_tenant_context
from ..repositories import (
    get_intake_packet_repository,
    get_patient_intake_assignment_repository,
)
from ..repositories.patient_intake_assignment import WRITABLE_STATUSES
from ..services.patient_intake_assignment_service import IntakeAssignmentService
from ..settings import get_settings
from .invite_email import (
    DEFAULT_TEMPLATE,
    InviteContext,
    InviteTemplate,
    RenderedInvite,
    describe_duration,
    render,
)

#: (patient_id, user_id, version ids about to be sent) -> form names, in order.
FormNames = Callable[[str, str, Iterable[str]], list[str]]


def get_invite_form_names(
    _ctx: Annotated[TenantContext, Depends(get_tenant_context)],
) -> FormNames:
    """The names of the forms waiting for a client, on the caller's practice."""
    service = IntakeAssignmentService(
        get_patient_intake_assignment_repository(), get_intake_packet_repository()
    )

    def name_of(version_id: str) -> str | None:
        version = service.version(version_id)
        if version is None:
            return None
        template = service.template(str(version["template_id"]))
        return None if template is None else str(template["name"])

    def lookup(patient_id: str, user_id: str, upcoming: Iterable[str]) -> list[str]:
        names: list[str] = []
        for row in service.list_for_clinician(patient_id, user_id):
            if str(row.get("status")) in WRITABLE_STATUSES:
                name = name_of(str(row["version_id"]))
                if name and name not in names:
                    names.append(name)
        for version_id in upcoming:
            name = name_of(version_id)
            if name and name not in names:
                names.append(name)
        return names

    return lookup


@dataclass(frozen=True)
class InviteFacts:
    client_first_name: str
    practice_name: str
    forms: list[str]
    #: The client's primary clinician, or ``None`` to name the practice
    #: instead (see ``app.portal.clinicians``).
    clinician_name: str | None = None


def link_expiry() -> str:
    """How long the link works, from the same setting the token is minted with."""
    return describe_duration(get_settings().portal_invite_ttl_seconds)


def compose(template: InviteTemplate | None, facts: InviteFacts, link: str) -> RenderedInvite:
    """Fill the template in for one client.

    With no clinician's name to give, the practice's name stands in, so the
    email never says "has invited you" with nobody in front of it.
    """
    clinician_name = (facts.clinician_name or "").strip() or facts.practice_name
    return render(
        template or DEFAULT_TEMPLATE,
        InviteContext(
            portal_link=link,
            client_first_name=facts.client_first_name,
            practice_name=facts.practice_name,
            forms=facts.forms,
            link_expiry=link_expiry(),
            clinician_name=clinician_name,
        ),
    )
