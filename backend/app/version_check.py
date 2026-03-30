# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Client version enforcement.

Reads the server version from backend/VERSION and minimum client versions
from the repo-root min_client_versions.json.

Clients send X-Client-Version and X-Client-Platform headers.
If the client version is below the configured minimum for that platform,
the request is rejected with 426 Upgrade Required.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

Platform = Literal["web", "macos", "windows"]

VALID_PLATFORMS: set[str] = {"web", "macos", "windows"}

_SEMVER_PART_COUNT = 3  # major.minor.patch

# backend/ is two levels up from this file: app/ -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
# Repo root is one more level up: backend/ -> repo root
_REPO_ROOT = _BACKEND_ROOT.parent


@lru_cache
def get_server_version() -> str:
    """Read the server version from backend/VERSION."""
    return (_BACKEND_ROOT / "VERSION").read_text().strip()


@lru_cache
def get_min_versions() -> dict[str, str]:
    """Read minimum required client versions from min_client_versions.json."""
    versions_file = _REPO_ROOT / "min_client_versions.json"
    data: dict[str, str] = json.loads(versions_file.read_text())
    return data


def parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver string into a 3-part comparable tuple.

    Accepts "1.0.0", "1.2", "1" — missing parts are padded with 0.
    """
    try:
        parts = [int(p) for p in version.strip().split(".")]
    except (ValueError, AttributeError) as err:
        raise ValueError(f"Invalid version: {version!r}") from err
    # Pad to exactly major.minor.patch so (1,0) doesn't compare < (1,0,0)
    parts.extend(0 for _ in range(_SEMVER_PART_COUNT - len(parts)))
    return (parts[0], parts[1], parts[2])


def is_version_outdated(client_version: str, min_version: str) -> bool:
    """Return True if client_version is strictly below min_version."""
    return parse_semver(client_version) < parse_semver(min_version)


def check_client_version(request: Request) -> None:
    """Check X-Client-Version / X-Client-Platform headers and reject outdated clients.

    If headers are missing, the request is allowed through (backwards compatibility
    with clients that predate version checking).

    Raises:
        HTTPException: 426 Upgrade Required if the client is too old.
    """
    client_version = request.headers.get("X-Client-Version")
    client_platform = request.headers.get("X-Client-Platform")

    if not client_version or not client_platform:
        return

    platform = client_platform.lower()
    if platform not in VALID_PLATFORMS:
        logger.warning("Unknown client platform: %s", client_platform)
        return

    min_versions = get_min_versions()
    min_version = min_versions[platform]

    try:
        if is_version_outdated(client_version, min_version):
            logger.info(
                "Blocked outdated %s client v%s (minimum: %s)",
                platform,
                client_version,
                min_version,
            )
            raise HTTPException(
                status_code=status.HTTP_426_UPGRADE_REQUIRED,
                detail={
                    "error": {
                        "code": "CLIENT_UPDATE_REQUIRED",
                        "message": (
                            f"Your app version ({client_version}) is no longer supported. "
                            f"Please update to version {min_version} or later."
                        ),
                        "details": {
                            "platform": platform,
                            "current_version": client_version,
                            "min_version": min_version,
                        },
                    }
                },
            )
    except ValueError:
        logger.warning("Unparseable client version: %s", client_version)
