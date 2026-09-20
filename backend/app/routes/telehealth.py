# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Telehealth settings: which video services are on offer, and connecting Zoom.

Two surfaces, and they are different questions. ``/providers`` is what the
clinician may choose between, which is the deployment's list narrowed to what
this clinician has actually connected — the settings page and the appointment
form both render from it, so neither can offer a room that would never
arrive. The Zoom routes are the connect flow behind one entry on that list.

The Zoom flow is the calendar's flow with the vendor swapped: a signed
``state`` binds the authorization request to the clinician who started it, the
redirect URI is checked against the deployment's own list before a code is
spent, and the grant is encrypted before it reaches a column. Zoom does not
offer PKCE, which is why that one step is missing and nothing else is.

Nothing here reads a chart, so nothing here writes an audit entry. What a
clinician connected is their own account's business; the appointments the
connection later produces are audited where appointments always are.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..api_errors import BadRequestError, NotFoundError
from ..auth.oauth_redirect import is_allowed_oauth_redirect_uri
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_active_subscription,
    require_baa_acceptance,
)
from ..calendar_providers.oauth_state import OAuthStateError, mint_state, verify_state
from ..meeting_providers.registry import build_registry
from ..meeting_providers.zoom_client import (
    ZoomError,
    authorize_url,
    exchange_code,
    revoke,
)
from ..repositories import UserRepository, get_user_repository
from ..repositories import get_google_calendar_token_repository as _gcal_token_repo_factory
from ..repositories import get_zoom_connection_store as _zoom_store_factory
from ..services.telehealth import Clinician, MeetingProviderRegistry
from ..services.token_encryption import derive_subkey
from ..settings import get_settings

if TYPE_CHECKING:
    from ..models import User
    from ..repositories.postgres.telehealth_connection import PostgresZoomConnectionStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/telehealth",
    tags=["telehealth"],
    dependencies=[Depends(require_active_subscription)],
)

_STATE_PURPOSE = "zoom-oauth-state"


def _state_key() -> bytes:
    return derive_subkey(_STATE_PURPOSE)


def get_zoom_store(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PostgresZoomConnectionStore:
    """The Zoom connection store, scoped to the caller's practice schema."""
    return _zoom_store_factory()


class TelehealthProviderOption(BaseModel):
    """One video service, as a settings page or a booking form shows it."""

    id: str
    display_name: str
    #: Whether this clinician can be given a room by it right now. A provider
    #: that is offered by the deployment but not connected is listed with
    #: this false, so the page can say "connect Zoom" rather than silently
    #: leaving it out and giving the reader nothing to act on.
    connected: bool


class TelehealthProvidersResponse(BaseModel):
    providers: list[TelehealthProviderOption]
    #: The clinician's current default, or None when they have not chosen one
    #: this deployment offers.
    default_provider: str | None = None
    #: Their own permanent room, for a service that works that way.
    room_url: str | None = None
    join_window_before_minutes: int


class ZoomAuthResponse(BaseModel):
    auth_url: str


class ZoomStatusResponse(BaseModel):
    connected: bool
    #: What Zoom calls the connected account, when it told us. Never a
    #: patient's anything.
    account_handle: str | None = None


class SetRoomUrlRequest(BaseModel):
    """The clinician's own permanent video room."""

    room_url: str | None = Field(
        default=None,
        max_length=500,
        description="Paste the room's web address, or leave empty to remove it.",
    )


@router.get("/providers", response_model=TelehealthProvidersResponse)
def list_providers(
    ctx: TenantContext = Depends(get_tenant_context),
    user_repo: UserRepository = Depends(get_user_repository),
    zoom_store: PostgresZoomConnectionStore = Depends(get_zoom_store),
) -> TelehealthProvidersResponse:
    """The video services this clinician may hold a session on.

    Not audited, and classified as such: it describes the deployment's
    configuration and this clinician's own connections, and discloses nothing
    about any patient.
    """
    settings = get_settings()
    preferences = user_repo.get_preferences(ctx.user_id)
    clinician = Clinician(
        id=ctx.user_id,
        preferred_provider=preferences.default_video_platform,
        room_url=preferences.telehealth_room_url,
    )
    registry = build_registry(
        settings,
        zoom_store=zoom_store,
        is_calendar_connected=_gcal_token_repo_factory().exists,
    )
    offered = set(registry.offered_to(clinician))
    providers = [
        TelehealthProviderOption(
            id=provider_id,
            display_name=_display_name(registry, provider_id),
            connected=provider_id in offered,
        )
        for provider_id in registry.ids()
    ]
    return TelehealthProvidersResponse(
        providers=providers,
        default_provider=preferences.default_video_platform
        if preferences.default_video_platform in offered
        else None,
        room_url=preferences.telehealth_room_url,
        join_window_before_minutes=settings.telehealth_join_window_before_minutes,
    )


def _display_name(registry: MeetingProviderRegistry, provider_id: str) -> str:
    provider = registry.get(provider_id)
    return str(getattr(provider, "display_name", provider_id))


@router.put("/room-url", response_model=SetRoomUrlRequest)
def set_room_url(
    request: SetRoomUrlRequest,
    ctx: TenantContext = Depends(get_tenant_context),
    user_repo: UserRepository = Depends(get_user_repository),
) -> SetRoomUrlRequest:
    """Record the clinician's own permanent video room.

    Refuses anything that is not an ``https`` web address. A room URL is
    handed to patients, so a value that turned out to be a ``javascript:``
    string or a plain-text link would be given to somebody to click.

    Not audited: it is the clinician's own setting, and no patient appears in
    it.
    """
    room_url = (request.room_url or "").strip() or None
    if room_url is not None and not room_url.startswith("https://"):
        raise BadRequestError("A room address must start with https://")
    preferences = user_repo.get_preferences(ctx.user_id)
    preferences.telehealth_room_url = room_url
    user_repo.save_preferences(ctx.user_id, preferences)
    return SetRoomUrlRequest(room_url=room_url)


@router.get("/zoom/authorize", response_model=ZoomAuthResponse)
def zoom_authorize(
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    ctx: TenantContext = Depends(get_tenant_context),
    _user: User = Depends(require_baa_acceptance),
) -> ZoomAuthResponse:
    """Where to send the clinician to approve a Zoom connection.

    Not audited: it starts a connection to the clinician's own Zoom account
    and reads nothing about anybody.
    """
    settings = get_settings()
    if not settings.zoom_client_id:
        raise NotFoundError("Zoom is not configured for this deployment")
    if not is_allowed_oauth_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
    return ZoomAuthResponse(
        auth_url=authorize_url(
            client_id=settings.zoom_client_id,
            redirect_uri=redirect_uri,
            state=mint_state(_state_key(), ctx.user_id),
        )
    )


@router.get("/zoom/callback", response_model=ZoomStatusResponse)
def zoom_callback(
    code: str = Query(..., description="OAuth authorization code"),
    redirect_uri: str = Query(..., description="OAuth redirect URI"),
    state: str = Query("", description="The state minted when authorization started"),
    ctx: TenantContext = Depends(get_tenant_context),
    _user: User = Depends(require_baa_acceptance),
    zoom_store: PostgresZoomConnectionStore = Depends(get_zoom_store),
) -> ZoomStatusResponse:
    """Exchange the authorization code for a stored grant.

    The state is required and is checked before the code is spent, so a code
    can only be exchanged by the person whose authorization request produced
    it. Declared with an empty default rather than as a required parameter so
    a missing one is refused exactly like a bad one.

    Not audited: the connection is to the clinician's own Zoom account.
    """
    settings = get_settings()
    if not settings.zoom_client_id:
        raise NotFoundError("Zoom is not configured for this deployment")
    if not is_allowed_oauth_redirect_uri(redirect_uri):
        raise BadRequestError("Invalid redirect_uri")
    try:
        verify_state(_state_key(), state, ctx.user_id)
    except OAuthStateError as exc:
        logger.warning("zoom_callback_rejected_state")
        raise BadRequestError("Invalid state") from exc

    try:
        grant = exchange_code(
            client_id=settings.zoom_client_id,
            client_secret=settings.zoom_client_secret.get_secret_value(),
            code=code,
            redirect_uri=redirect_uri,
        )
    except ZoomError as exc:
        logger.warning("zoom_callback_exchange_failed")
        raise BadRequestError("Could not complete the Zoom connection") from exc

    zoom_store.save(ctx.user_id, grant)
    return ZoomStatusResponse(connected=True, account_handle=grant.account_handle)


@router.delete("/zoom/disconnect", response_model=ZoomStatusResponse)
def zoom_disconnect(
    ctx: TenantContext = Depends(get_tenant_context),
    zoom_store: PostgresZoomConnectionStore = Depends(get_zoom_store),
) -> ZoomStatusResponse:
    """Forget the stored grant, and tell Zoom if we can.

    The row goes whether or not Zoom acknowledges the revoke. A clinician who
    pressed Disconnect and is still connected because a vendor was having a
    bad minute is the worse failure of the two, and the grant we kept would
    be the thing still able to act.

    Not audited: the clinician's own account.
    """
    settings = get_settings()
    grant = zoom_store.get(ctx.user_id)
    if grant is None:
        raise NotFoundError("Zoom is not connected")
    if settings.zoom_client_id:
        revoke(
            client_id=settings.zoom_client_id,
            client_secret=settings.zoom_client_secret.get_secret_value(),
            grant=grant,
        )
    zoom_store.delete(ctx.user_id)
    return ZoomStatusResponse(connected=False)


@router.get("/zoom/status", response_model=ZoomStatusResponse)
def zoom_status(
    ctx: TenantContext = Depends(get_tenant_context),
    zoom_store: PostgresZoomConnectionStore = Depends(get_zoom_store),
) -> ZoomStatusResponse:
    """Whether this clinician's Zoom account is connected.

    Not audited: a fact about the clinician's own integrations.
    """
    grant = zoom_store.get(ctx.user_id)
    if grant is None:
        return ZoomStatusResponse(connected=False)
    return ZoomStatusResponse(connected=True, account_handle=grant.account_handle)
