# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The single-VM install's update notice, and the button that asks for it.

* ``GET /api/software-update`` — the running release, the latest published
  one, whether it is newer, and the progress of an update under way.
* ``POST /api/software-update`` — ask the host to install the latest release.

Both are for a practice owner or admin (``require_practice_admin``), and both
answer 404 unless ``self_update_enabled`` is on and the host has recorded the
latest release, so a deployment that cannot update itself shows nothing.

This process never decides what gets installed. The host (``deploy/vm``)
checks GitHub for the latest release and writes ``host/latest.json`` and
``host/status.json``, mounted read-only here; the request this process writes
says only who asked and when. The host ignores its contents, finds the latest
release again itself, takes a backup, updates, and goes back to the previous
release if the new one does not come up. No PHI in any of these files.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import release_version
from ..api_errors import ConflictError, NotFoundError
from ..auth.route_access import subscription_exempt
from ..auth.service import get_current_user
from ..db.platform_models import (
    PracticeRow,  # noqa: TC001 — fastapi resolves the annotation at runtime
)
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..services import AuditService, get_audit_service
from ..settings import Settings, get_settings
from ..utcnow import utc_now
from ..version_check import is_newer_release
from .users import require_practice_admin

logger = logging.getLogger(__name__)

# Updating works in any subscription state: a release can carry a security fix.
router = APIRouter(
    prefix="/api/software-update",
    tags=["software-update"],
    dependencies=[Depends(subscription_exempt)],
)

UpdateState = Literal["idle", "updating", "done", "failed", "rolled_back"]


class LatestRelease(BaseModel):
    """``host/latest.json``, written by ``pablo-update --check``."""

    version: str
    notes_url: str
    checked_at: str


class UpdateStatus(BaseModel):
    """``host/status.json``, written by ``pablo-update-request`` as it goes."""

    model_config = ConfigDict(populate_by_name=True)

    state: UpdateState
    from_version: str = Field(default="", alias="from")
    to_version: str = Field(default="", alias="to")
    message: str = ""
    at: str = ""


class SoftwareUpdateResponse(BaseModel):
    current: str
    latest: str
    notes_url: str
    available: bool
    status: UpdateStatus | None


class SoftwareUpdateRequested(BaseModel):
    requested: str


def _read_model[M: BaseModel](path: Path, model: type[M]) -> M | None:
    """The file parsed as *model*, or None when it is missing or unreadable."""
    try:
        return model.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError):
        logger.warning("Ignoring unreadable update file %s", path.name)
        return None


def _update_dir(settings: Settings) -> Path:
    """The update directory, or 404 when this deployment does not update itself."""
    if not settings.self_update_enabled:
        raise NotFoundError("Not found")
    return Path(settings.self_update_dir)


def _latest(update_dir: Path) -> LatestRelease:
    latest = _read_model(update_dir / "host" / "latest.json", LatestRelease)
    if latest is None:
        raise NotFoundError("Not found")
    return latest


def _status(update_dir: Path) -> UpdateStatus | None:
    return _read_model(update_dir / "host" / "status.json", UpdateStatus)


def _write_atomically(path: Path, payload: dict[str, str]) -> None:
    """Write *path* whole or not at all: the host acts the moment it appears."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".update-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.get("")
def get_software_update(
    _practice: PracticeRow = Depends(require_practice_admin),
    settings: Settings = Depends(get_settings),
) -> SoftwareUpdateResponse:
    """The running and the latest release, and any update under way."""
    update_dir = _update_dir(settings)
    latest = _latest(update_dir)
    current = release_version()
    return SoftwareUpdateResponse(
        current=current,
        latest=latest.version,
        notes_url=latest.notes_url,
        available=is_newer_release(latest.version, current),
        status=_status(update_dir),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def request_software_update(
    http_request: Request,
    user: User = Depends(get_current_user),
    practice: PracticeRow = Depends(require_practice_admin),
    settings: Settings = Depends(get_settings),
    audit: AuditService = Depends(get_audit_service),
) -> SoftwareUpdateRequested:
    """Ask the host to install the latest release; it answers in ``status.json``."""
    update_dir = _update_dir(settings)
    latest = _latest(update_dir)
    current = release_version()
    in_progress = _status(update_dir)
    if in_progress is not None and in_progress.state == "updating":
        raise ConflictError("An update is already running", code="UPDATE_IN_PROGRESS")
    if not is_newer_release(latest.version, current):
        raise ConflictError("This is already the latest release", code="NO_NEWER_RELEASE")

    audit.log(
        AuditAction.SOFTWARE_UPDATE_REQUESTED,
        user,
        http_request,
        resource_type=ResourceType.PRACTICE,
        resource_id=practice.id,
        changes={"from_version": current, "to_version": latest.version},
    )
    _write_atomically(
        update_dir / "requests" / "update.json",
        {"requested_by": user.id, "at": utc_now().isoformat()},
    )
    logger.info("Software update to %s requested by %s", latest.version, user.id)
    return SoftwareUpdateRequested(requested=latest.version)
