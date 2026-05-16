# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for the companion device registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from threading import Lock

from ..models.companion_device import CompanionDevice
from ..utcnow import utc_now


class CompanionDeviceRepository(ABC):
    """Abstract store for native companion device enrollments."""

    @abstractmethod
    def upsert(self, device: CompanionDevice) -> None:
        """Insert or update by install_id.

        Re-enrollment of the same install_id (e.g. companion lost its
        Keychain row and re-generated a key) overwrites the existing
        row's pubkey, jkt, key_storage, and platform metadata, and
        bumps ``last_seen``. ``enrolled_at`` is preserved on update —
        the install_id was first seen at that timestamp.
        """

    @abstractmethod
    def get(self, install_id: str) -> CompanionDevice | None: ...

    @abstractmethod
    def touch_last_seen(self, install_id: str, when: datetime | None = None) -> None:
        """Update last_seen for an active device. No-op if missing or revoked."""


class InMemoryCompanionDeviceRepository(CompanionDeviceRepository):
    def __init__(self) -> None:
        self._rows: dict[str, CompanionDevice] = {}
        self._lock = Lock()

    def upsert(self, device: CompanionDevice) -> None:
        with self._lock:
            existing = self._rows.get(device.install_id)
            enrolled_at = existing.enrolled_at if existing is not None else device.enrolled_at
            self._rows[device.install_id] = CompanionDevice(
                install_id=device.install_id,
                user_id=device.user_id,
                device_public_key_jwk=device.device_public_key_jwk,
                jkt=device.jkt,
                key_storage=device.key_storage,
                platform=device.platform,
                os_version=device.os_version,
                hostname_hash=device.hostname_hash,
                enrolled_at=enrolled_at,
                last_seen=device.last_seen,
                revoked_at=device.revoked_at,
            )

    def get(self, install_id: str) -> CompanionDevice | None:
        with self._lock:
            return self._rows.get(install_id)

    def touch_last_seen(self, install_id: str, when: datetime | None = None) -> None:
        ts = when or utc_now()
        with self._lock:
            existing = self._rows.get(install_id)
            if existing is None or existing.revoked_at is not None:
                return
            self._rows[install_id] = CompanionDevice(
                install_id=existing.install_id,
                user_id=existing.user_id,
                device_public_key_jwk=existing.device_public_key_jwk,
                jkt=existing.jkt,
                key_storage=existing.key_storage,
                platform=existing.platform,
                os_version=existing.os_version,
                hostname_hash=existing.hostname_hash,
                enrolled_at=existing.enrolled_at,
                last_seen=ts,
                revoked_at=existing.revoked_at,
            )
