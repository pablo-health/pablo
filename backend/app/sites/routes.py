# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice's website, as the practice manages it (Settings > Website).

* ``GET /api/practice/website`` — any clinician of the practice: what is
  published, where it is live, the draft, and the versions kept.
* ``POST /api/practice/website/draft`` — upload a zip of a static folder as
  the draft (multipart, field ``file``), replacing any draft.
* ``DELETE /api/practice/website/draft`` — discard the draft.
* ``POST /api/practice/website/draft/preview`` — a new address for previewing
  the draft, working for an hour (:mod:`app.sites.public_routes`).
* ``POST /api/practice/website/publish`` — publish the draft as a new version
  and make it live.
* ``POST /api/practice/website/versions/{version}/live`` — make a kept version
  live again (roll back).

The changes are for whoever may change the practice's domains — the owner
today (:func:`app.routes.practice_domains._manageable_practice_id`), so
widening one widens both. The practice is always the caller's own. Uploads,
publishes and roll backs are audited by the service, with the version, file
count and bytes. Old versions and replaced drafts are tidied away after the
change commits, as a background task. A publish or roll back also forgets this
process's answers about website hosts once it has committed, so it is served
here at once; another process notices within the minute it keeps answers.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic resolves it at runtime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Request, UploadFile
from pydantic import BaseModel

from ..api_errors import (
    APIError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from ..auth.service import require_active_subscription
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..routes.practice_domains import _manageable_practice_id, _practice_id
from .files import MAX_ARCHIVE_BYTES, SiteFilesError, SiteTooLargeError, read_zip
from .hosts import get_site_host_cache
from .service import (
    NoDraftError,
    PracticeSiteService,
    SiteNotConfiguredError,
    UnknownVersionError,
    get_practice_site_service,
    tidy_practice_site,
)

if TYPE_CHECKING:
    from collections.abc import Callable

router = APIRouter(prefix="/api/practice/website", tags=["practice-website"])


class SiteTooLargeAPIError(APIError):
    status_code = 413
    code = "SITE_TOO_LARGE"


class SiteDraftResponse(BaseModel):
    file_count: int
    total_bytes: int
    uploaded_at: datetime


class SiteVersionResponse(BaseModel):
    version: int
    file_count: int
    total_bytes: int
    published_at: datetime
    is_live: bool


class PracticeSiteResponse(BaseModel):
    #: Whether this deployment publishes websites at all.
    enabled: bool
    live_version: int | None
    #: The working website host the live version is served at; ``None`` while
    #: nothing is published or no website host works.
    live_host: str | None
    has_active_host: bool
    draft: SiteDraftResponse | None
    #: Kept versions, newest first.
    versions: list[SiteVersionResponse]


class SitePreviewResponse(BaseModel):
    #: The draft's address on the API's origin, ending in ``/``.
    path: str
    expires_at: datetime


def _response(service: PracticeSiteService, practice_id: str) -> PracticeSiteResponse:
    status = service.status(practice_id)
    draft = status.draft
    return PracticeSiteResponse(
        enabled=service.enabled,
        live_version=status.live_version,
        live_host=status.live_host,
        has_active_host=status.has_active_host,
        draft=SiteDraftResponse(
            file_count=draft.file_count,
            total_bytes=draft.total_bytes,
            uploaded_at=draft.uploaded_at,
        )
        if draft
        else None,
        versions=[
            SiteVersionResponse(
                version=v.version,
                file_count=v.file_count,
                total_bytes=v.total_bytes,
                published_at=v.published_at,
                is_live=v.version == status.live_version,
            )
            for v in status.versions
        ],
    )


def _translated[T](call: Callable[[], T]) -> T:
    """*call*'s answer, with the service's refusals as API errors."""
    try:
        return call()
    except SiteNotConfiguredError as exc:
        raise ServiceUnavailableError(
            "Websites aren't turned on for this deployment.", code="SITE_NOT_CONFIGURED"
        ) from exc
    except NoDraftError as exc:
        raise ConflictError("There's no draft. Upload one first.", code="NO_DRAFT") from exc
    except UnknownVersionError as exc:
        raise NotFoundError("That version isn't kept any more.", code="NO_SUCH_VERSION") from exc


@router.get("", response_model=PracticeSiteResponse)
def get_practice_site(
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> PracticeSiteResponse:
    """The practice's website: what is live, where, the draft and the kept versions."""
    return _response(service, _practice_id(user))


@router.post("/draft", response_model=PracticeSiteResponse)
def upload_practice_site_draft(
    http_request: Request,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> PracticeSiteResponse:
    """Make the uploaded zip the draft. 422 saying what is wrong with it; 413
    past a size limit."""
    practice_id = _manageable_practice_id(user)
    # Read on the worker thread this sync route runs on; one byte past the
    # limit is enough to know it is past it.
    data = file.file.read(MAX_ARCHIVE_BYTES + 1)
    try:
        site = read_zip(data)
    except SiteTooLargeError as exc:
        raise SiteTooLargeAPIError(str(exc)) from exc
    except SiteFilesError as exc:
        raise UnprocessableEntityError(str(exc), code="INVALID_SITE") from exc
    _translated(lambda: service.save_draft(practice_id, site, user, http_request))
    background.add_task(tidy_practice_site, practice_id)
    return _response(service, practice_id)


@router.delete("/draft", response_model=PracticeSiteResponse)
def discard_practice_site_draft(
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> PracticeSiteResponse:
    """Discard the draft."""
    practice_id = _manageable_practice_id(user)
    _translated(lambda: service.discard_draft(practice_id))
    background.add_task(tidy_practice_site, practice_id)
    return _response(service, practice_id)


@router.post("/draft/preview", response_model=SitePreviewResponse)
def preview_practice_site_draft(
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> SitePreviewResponse:
    """A new preview address for the draft. 409 with no draft."""
    practice_id = _manageable_practice_id(user)
    token, expires_at = _translated(lambda: service.mint_preview(practice_id))
    return SitePreviewResponse(
        path=f"/api/practice/website/preview/{token}/", expires_at=expires_at
    )


@router.post("/publish", response_model=PracticeSiteResponse)
def publish_practice_site(
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> PracticeSiteResponse:
    """Publish the draft as a new version and make it live. 409 with no draft."""
    practice_id = _manageable_practice_id(user)
    _translated(lambda: service.publish_draft(practice_id, user, http_request))
    background.add_task(get_site_host_cache().clear)
    background.add_task(tidy_practice_site, practice_id)
    return _response(service, practice_id)


@router.post("/versions/{version}/live", response_model=PracticeSiteResponse)
def roll_back_practice_site(
    version: int,
    http_request: Request,
    background: BackgroundTasks,
    user: User = Depends(require_active_subscription),
    service: PracticeSiteService = Depends(get_practice_site_service),
) -> PracticeSiteResponse:
    """Make a kept version live again. 404 if it is not kept."""
    practice_id = _manageable_practice_id(user)
    _translated(lambda: service.roll_back(practice_id, version, user, http_request))
    background.add_task(get_site_host_cache().clear)
    return _response(service, practice_id)
