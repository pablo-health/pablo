# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who the practice's email to its clients is from, and where replies go.

* ``GET /api/practice/email-sender`` — any clinician of the practice: the
  three settings, their defaults, the domain mail leaves from, and what the
  next email will carry.
* ``PUT /api/practice/email-sender`` — the practice owner: save the three
  settings. A blank field goes back to its default. 422 with what to fix.

The practice is always the caller's own, resolved from the caller. The save is
audited: not PHI, but where clients' replies go belongs on the record.

``applies`` is false where this deployment's email channel cannot send as the
practice (see :class:`~app.portal.delivery.PracticeSenderDelivery`). The screen
then says so rather than previewing a From line nobody will see.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..api_errors import ForbiddenError, NotFoundError, UnprocessableEntityError
from ..auth.service import require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..routes.users import _get_own_practice_as_owner, _resolve_practice_id_for
from ..services.audit_service import AuditService, get_audit_service
from .client_sender import (
    REPLY_TO_MAX,
    SENDER_NAME_MAX,
    SenderSettings,
    SenderSettingsError,
    SenderSettingsStore,
    SenderView,
    clean_local_part,
    clean_reply_to,
    clean_sender_name,
    deployment_from_address,
    get_sender_settings_store,
)
from .delivery import PortalInviteDelivery, PracticeSenderDelivery
from .factory import get_invite_delivery

router = APIRouter(prefix="/api/practice/email-sender", tags=["practice"])


class EmailSenderFields(BaseModel):
    """The three settings. ``None`` (or blank, on a save) is the default.

    The lengths here only cap what is read; the limits a person is told about
    are checked after trimming, by the ``clean_*`` functions.
    """

    sender_name: str | None = Field(default=None, max_length=SENDER_NAME_MAX * 2)
    sender_local_part: str | None = Field(default=None, max_length=128)
    reply_to: str | None = Field(default=None, max_length=REPLY_TO_MAX * 2)


class EmailSenderDefaults(BaseModel):
    sender_name: str
    sender_local_part: str


class EffectiveSender(BaseModel):
    from_name: str
    #: ``None``: the deployment's own address (``deployment_from_address``).
    from_address: str | None
    reply_to: str | None


class EmailSenderResponse(BaseModel):
    #: Whether the caller may change the settings (the practice owner).
    can_edit: bool
    #: False where this deployment's email channel sends under its own name.
    applies: bool
    chosen: EmailSenderFields
    defaults: EmailSenderDefaults
    #: The practice's domain mail leaves from, or ``None`` while none can send.
    sending_domain: str | None
    #: The deployment's own sending address, when it is known here.
    deployment_from_address: str | None
    effective: EffectiveSender


def _practice_id(user: User) -> str:
    practice_id = _resolve_practice_id_for(user)
    if practice_id is None:
        raise NotFoundError("No practice mapping for this account", code="NO_PRACTICE")
    return practice_id


def _can_edit(user: User) -> bool:
    try:
        _get_own_practice_as_owner(user)
    except ForbiddenError:
        return False
    return True


def _response(view: SenderView, *, can_edit: bool, applies: bool) -> EmailSenderResponse:
    return EmailSenderResponse(
        can_edit=can_edit,
        applies=applies,
        chosen=EmailSenderFields(
            sender_name=view.chosen.sender_name,
            sender_local_part=view.chosen.sender_local_part,
            reply_to=view.chosen.reply_to,
        ),
        defaults=EmailSenderDefaults(
            sender_name=view.defaults.sender_name,
            sender_local_part=view.defaults.sender_local_part,
        ),
        sending_domain=view.sending_domain,
        deployment_from_address=deployment_from_address(),
        effective=EffectiveSender(
            from_name=view.effective.from_name,
            from_address=view.effective.from_address,
            reply_to=view.effective.reply_to,
        ),
    )


@router.get("", response_model=EmailSenderResponse)
def get_email_sender(
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[SenderSettingsStore, Depends(get_sender_settings_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
) -> EmailSenderResponse:
    """The practice's client-email sender settings, and what they come to."""
    view = store.view(_practice_id(user))
    return _response(
        view, can_edit=_can_edit(user), applies=isinstance(delivery, PracticeSenderDelivery)
    )


@router.put("", response_model=EmailSenderResponse)
def save_email_sender(  # noqa: PLR0913 — FastAPI Depends-injected params are idiomatic
    body: EmailSenderFields,
    http_request: Request,
    user: Annotated[User, Depends(require_active_subscription)],
    store: Annotated[SenderSettingsStore, Depends(get_sender_settings_store)],
    delivery: Annotated[PortalInviteDelivery, Depends(get_invite_delivery)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> EmailSenderResponse:
    """Save the settings. Owner only (403 otherwise); 422 with what to fix."""
    practice_id = _get_own_practice_as_owner(user).id
    try:
        chosen = SenderSettings(
            sender_name=clean_sender_name(body.sender_name),
            sender_local_part=clean_local_part(body.sender_local_part),
            reply_to=clean_reply_to(body.reply_to),
        )
        view = store.save(practice_id, chosen, by=user.id)
    except SenderSettingsError as exc:
        raise UnprocessableEntityError(str(exc), code="EMAIL_SENDER_INVALID") from None
    audit.log(
        AuditAction.PRACTICE_EMAIL_SENDER_CHANGED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice_id,
        changes={
            "sender_name": chosen.sender_name,
            "sender_local_part": chosen.sender_local_part,
            "reply_to": chosen.reply_to,
        },
    )
    return _response(view, can_edit=True, applies=isinstance(delivery, PracticeSenderDelivery))
