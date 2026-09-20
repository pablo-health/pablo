# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A thin Zoom REST client: the OAuth round trip, and four meeting calls.

Only what the adapter beside it needs. Every request goes through the shared
outbound-retry engine, and every failure is one exception type, so nothing
above this module has to reason about Zoom's status codes.

Endpoints and grant parameters are the vendor's:
https://developers.zoom.us/docs/integrations/oauth/

Two properties of the stored grant are load-bearing and easy to lose:

* **The refresh token rotates.** Zoom issues a new one with every refresh and
  retires the one presented, so a caller that keeps the old value has a
  connection that works once. :func:`refresh_grant` hands back the whole new
  grant and the caller is expected to store all of it.
* **The access token expires in an hour**, which is shorter than the gap
  between two appointments. So nothing caches one across requests; each call
  refreshes if the stored one is spent.

Log lines carry the path and the upstream status. Never a token, never a
meeting id, never anything about who the meeting is with.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx

from ..reliability import HTTP_REQUEST, Idempotency, RetryExhaustedError, call_with_retry
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

ZOOM_OAUTH_BASE = "https://zoom.us"
ZOOM_API_BASE = "https://api.zoom.us/v2"

_REQUEST_TIMEOUT_SECONDS = 10.0

#: Refresh this far before the access token actually expires, so a call that
#: starts just under the wire does not finish just over it.
_EXPIRY_MARGIN = timedelta(seconds=120)

#: A scheduled meeting. The vendor's enum; 2 is the only value Pablo books.
#: https://raw.githubusercontent.com/zoom/api/master/openapi.v2.json
MEETING_TYPE_SCHEDULED = 2


class ZoomError(RuntimeError):
    """Zoom could not be reached, or refused something we asked for."""


@dataclass(frozen=True)
class ZoomGrant:
    """One clinician's OAuth grant, as it is stored and as it is refreshed."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    account_handle: str | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return (now or utc_now()) >= self.expires_at - _EXPIRY_MARGIN


def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode('ascii')}"


def authorize_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the clinician to approve the connection."""
    query = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{ZOOM_OAUTH_BASE}/oauth/authorize?{query}"


def _token_call(
    *,
    client_id: str,
    client_secret: str,
    data: dict[str, str],
) -> dict[str, Any]:
    def _call() -> httpx.Response:
        response = httpx.post(
            f"{ZOOM_OAUTH_BASE}/oauth/token",
            data=data,
            headers={"Authorization": _basic_auth(client_id, client_secret)},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response

    try:
        response = call_with_retry(_call, policy=HTTP_REQUEST, idempotency=Idempotency.UNSAFE)
    except RetryExhaustedError as exc:
        logger.warning("zoom_token_unreachable grant=%s", data.get("grant_type"))
        raise ZoomError("could not reach Zoom") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "zoom_token_refused grant=%s status=%d",
            data.get("grant_type"),
            exc.response.status_code,
        )
        raise ZoomError("Zoom refused the grant") from exc
    return dict(response.json())


def _grant_from_payload(payload: dict[str, Any], *, account_handle: str | None) -> ZoomGrant:
    try:
        access_token = str(payload["access_token"])
        refresh_token = str(payload["refresh_token"])
        expires_in = int(payload["expires_in"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ZoomError("Zoom returned a grant we cannot read") from exc
    return ZoomGrant(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=utc_now() + timedelta(seconds=expires_in),
        account_handle=account_handle,
    )


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> ZoomGrant:
    """Turn an authorization code into a stored grant."""
    payload = _token_call(
        client_id=client_id,
        client_secret=client_secret,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    return _grant_from_payload(payload, account_handle=None)


def refresh_grant(
    *,
    client_id: str,
    client_secret: str,
    grant: ZoomGrant,
) -> ZoomGrant:
    """Exchange the refresh token for a new grant, INCLUDING a new refresh token."""
    payload = _token_call(
        client_id=client_id,
        client_secret=client_secret,
        data={"grant_type": "refresh_token", "refresh_token": grant.refresh_token},
    )
    return _grant_from_payload(payload, account_handle=grant.account_handle)


def revoke(*, client_id: str, client_secret: str, grant: ZoomGrant) -> bool:
    """Tell Zoom the connection is over. False if it could not be told.

    A revoke that fails still disconnects on our side — leaving a row behind
    because the vendor was unreachable would mean a clinician who pressed
    Disconnect stays connected.
    """
    try:
        _token_call(
            client_id=client_id,
            client_secret=client_secret,
            data={"token": grant.access_token},
        )
    except ZoomError:
        logger.warning("zoom_revoke_failed")
        return False
    return True


def api_request(
    method: str,
    path: str,
    *,
    access_token: str,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the Zoom v2 API. Raises :class:`ZoomError` on anything but success.

    An empty body — which DELETE answers — comes back as an empty mapping
    rather than as a parse failure.
    """

    def _call() -> httpx.Response:
        response = httpx.request(
            method,
            f"{ZOOM_API_BASE}{path}",
            json=json_body,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response

    idempotency = Idempotency.SAFE if method.upper() in {"GET", "DELETE"} else Idempotency.UNSAFE
    try:
        response = call_with_retry(_call, policy=HTTP_REQUEST, idempotency=idempotency)
    except RetryExhaustedError as exc:
        logger.warning("zoom_api_unreachable path=%s", path)
        raise ZoomError("could not reach Zoom") from exc
    except httpx.HTTPStatusError as exc:
        logger.warning("zoom_api_refused path=%s status=%d", path, exc.response.status_code)
        raise ZoomError("Zoom refused the request") from exc
    if not response.content:
        return {}
    return dict(response.json())
