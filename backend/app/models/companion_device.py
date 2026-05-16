# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain + API types for native companion device enrollment.

A companion device is a Mac or Windows install of the native Pablo
Companion app. Enrollment happens at the OAuth code-exchange path
(:mod:`backend.app.routes.auth`); the companion submits an install_id
and a Secure-Enclave / TPM-backed public key, the backend records it.

See ``docs/design/companion-thin-client.md`` § Enrollment and the
THERAPY-xo0o / THERAPY-6qtr beads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DevicePlatform = Literal["mac", "windows", "linux"]
KeyStorage = Literal["hardware", "software"]


class CompanionEnrollment(BaseModel):
    """Enrollment payload submitted alongside the OAuth code exchange."""

    install_id: str = Field(min_length=8, max_length=64)
    platform: DevicePlatform
    os_version: str | None = Field(default=None, max_length=64)
    hostname_hash: str | None = Field(default=None, max_length=64)
    device_public_key_jwk: dict[str, str]
    key_storage: KeyStorage


@dataclass(frozen=True)
class CompanionDevice:
    install_id: str
    user_id: str
    device_public_key_jwk: dict[str, str]
    jkt: str
    key_storage: KeyStorage
    platform: DevicePlatform
    os_version: str | None
    hostname_hash: str | None
    enrolled_at: datetime
    last_seen: datetime
    revoked_at: datetime | None
