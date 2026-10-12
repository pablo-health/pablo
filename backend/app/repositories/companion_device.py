# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for the companion device registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from threading import Lock

from ..models.companion_device import CompanionDevice
from ..utcnow import utc_now

# How fresh ``last_seen`` is kept. It answers "is this device still in use",
# which minute-level precision serves; writing it on every request would turn
# every companion call into a write on one shared row.
LAST_SEEN_RESOLUTION = timedelta(seconds=60)


class CompanionDeviceRepository(ABC):
    """Abstract store for native companion device enrollments."""

    @abstractmethod
    def upsert(self, device: CompanionDevice) -> None:
        """Insert or update by ``(user_id, install_id)``.

        Re-enrollment of the same install_id by the same user (e.g. the
        companion lost its Keychain row and re-generated a key) overwrites
        that user's row: pubkey, jkt, key_storage, and platform metadata,
        and bumps ``last_seen``. ``enrolled_at`` is preserved on update —
        the user first enrolled the install at that timestamp. Another
        user's row for the same install_id is never touched.
        """

    @abstractmethod
    def get(self, user_id: str, install_id: str) -> CompanionDevice | None:
        """Return this user's enrollment of ``install_id``, or None."""

    @abstractmethod
    def list_for_user(self, user_id: str) -> list[CompanionDevice]:
        """Return the user's active (non-revoked) enrolled devices.

        Scoped by ``user_id`` — the platform table has no RLS, so the
        ``user_id`` filter IS the access-control boundary. Revoked rows
        (``revoked_at IS NOT NULL``) are excluded.
        """

    @abstractmethod
    def touch_last_seen(self, user_id: str, install_id: str, when: datetime | None = None) -> None:
        """Update last_seen for this user's active enrollment of ``install_id``.

        No-op if the device is missing or revoked, or was already seen within
        ``LAST_SEEN_RESOLUTION``. Never waits on a concurrent writer.
        """


class InMemoryCompanionDeviceRepository(CompanionDeviceRepository):
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], CompanionDevice] = {}
        self._lock = Lock()

    def upsert(self, device: CompanionDevice) -> None:
        with self._lock:
            key = (device.user_id, device.install_id)
            existing = self._rows.get(key)
            enrolled_at = existing.enrolled_at if existing is not None else device.enrolled_at
            self._rows[key] = CompanionDevice(
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

    def get(self, user_id: str, install_id: str) -> CompanionDevice | None:
        with self._lock:
            return self._rows.get((user_id, install_id))

    def list_for_user(self, user_id: str) -> list[CompanionDevice]:
        with self._lock:
            return [
                row
                for row in self._rows.values()
                if row.user_id == user_id and row.revoked_at is None
            ]

    def touch_last_seen(self, user_id: str, install_id: str, when: datetime | None = None) -> None:
        ts = when or utc_now()
        key = (user_id, install_id)
        with self._lock:
            existing = self._rows.get(key)
            if existing is None or existing.revoked_at is not None:
                return
            if existing.last_seen >= ts - LAST_SEEN_RESOLUTION:
                return
            self._rows[key] = CompanionDevice(
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
