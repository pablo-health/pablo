# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of CompanionDeviceRepository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from ...db.platform_models import CompanionDeviceRow
from ...models.companion_device import CompanionDevice, DevicePlatform, KeyStorage
from ...utcnow import utc_now
from ..companion_device import LAST_SEEN_RESOLUTION, CompanionDeviceRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresCompanionDeviceRepository(CompanionDeviceRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, device: CompanionDevice) -> None:
        stmt = (
            insert(CompanionDeviceRow)
            .values(
                install_id=device.install_id,
                user_id=device.user_id,
                device_public_key_jwk=device.device_public_key_jwk,
                jkt=device.jkt,
                key_storage=device.key_storage,
                platform=device.platform,
                os_version=device.os_version,
                hostname_hash=device.hostname_hash,
                enrolled_at=device.enrolled_at,
                last_seen=device.last_seen,
                revoked_at=device.revoked_at,
            )
            .on_conflict_do_update(
                # The key is (install_id, user_id), so a conflict is always this
                # user re-enrolling this install: it rotates this user's key and
                # never reaches another user's row for the same install.
                index_elements=[CompanionDeviceRow.install_id, CompanionDeviceRow.user_id],
                set_={
                    "device_public_key_jwk": device.device_public_key_jwk,
                    "jkt": device.jkt,
                    "key_storage": device.key_storage,
                    "platform": device.platform,
                    "os_version": device.os_version,
                    "hostname_hash": device.hostname_hash,
                    "last_seen": device.last_seen,
                },
                # Revocation is sticky: never reset revoked_at, and never replace
                # a revoked row's key. The service rejects re-enrolling a revoked
                # device up front; this guard covers a revoke that lands between
                # that check and this write. Reactivation is a separate action.
                where=CompanionDeviceRow.revoked_at.is_(None),
            )
        )
        self._session.execute(stmt)
        self._session.flush()

    def get(self, user_id: str, install_id: str) -> CompanionDevice | None:
        stmt = select(CompanionDeviceRow).where(
            CompanionDeviceRow.install_id == install_id,
            CompanionDeviceRow.user_id == user_id,
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return CompanionDevice(
            install_id=row.install_id,
            user_id=row.user_id,
            device_public_key_jwk=row.device_public_key_jwk,
            jkt=row.jkt,
            key_storage=cast("KeyStorage", row.key_storage),
            platform=cast("DevicePlatform", row.platform),
            os_version=row.os_version,
            hostname_hash=row.hostname_hash,
            enrolled_at=row.enrolled_at,
            last_seen=row.last_seen,
            revoked_at=row.revoked_at,
        )

    def list_for_user(self, user_id: str) -> list[CompanionDevice]:
        stmt = (
            select(CompanionDeviceRow)
            .where(
                CompanionDeviceRow.user_id == user_id,
                CompanionDeviceRow.revoked_at.is_(None),
            )
            .order_by(CompanionDeviceRow.enrolled_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [
            CompanionDevice(
                install_id=row.install_id,
                user_id=row.user_id,
                device_public_key_jwk=row.device_public_key_jwk,
                jkt=row.jkt,
                key_storage=cast("KeyStorage", row.key_storage),
                platform=cast("DevicePlatform", row.platform),
                os_version=row.os_version,
                hostname_hash=row.hostname_hash,
                enrolled_at=row.enrolled_at,
                last_seen=row.last_seen,
                revoked_at=row.revoked_at,
            )
            for row in rows
        ]

    def touch_last_seen(self, user_id: str, install_id: str, when: datetime | None = None) -> None:
        ts = when or utc_now()
        # SKIP LOCKED rather than a plain UPDATE: a companion sends several
        # requests at once for the same enrollment, and a plain UPDATE queues
        # each one behind whichever request locked the row first, until it
        # hits lock_timeout. Skipping is safe because the request holding the
        # lock is recording the same fact.
        claimable = (
            select(CompanionDeviceRow.install_id, CompanionDeviceRow.user_id)
            .where(
                CompanionDeviceRow.install_id == install_id,
                CompanionDeviceRow.user_id == user_id,
                CompanionDeviceRow.revoked_at.is_(None),
                CompanionDeviceRow.last_seen < ts - LAST_SEEN_RESOLUTION,
            )
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(CompanionDeviceRow)
            .where(tuple_(CompanionDeviceRow.install_id, CompanionDeviceRow.user_id).in_(claimable))
            .values(last_seen=ts)
        )
        self._session.execute(stmt)
