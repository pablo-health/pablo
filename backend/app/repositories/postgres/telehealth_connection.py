# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a clinician's video-service grant is kept, encrypted.

The grant itself never reaches a column in the clear: it is AES-256-GCM
encrypted through ``app.services.token_encryption`` on the way in and
decrypted on the way out, exactly as the calendar grant is, because it can
create meetings in the clinician's own account.

``account_handle`` is the one field kept readable, so a settings page can say
which account is connected without decrypting a grant to find out. It is the
clinician's own account label and never a patient's anything.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ...db.models import TelehealthConnectionRow
from ...meeting_providers.zoom_client import ZoomGrant
from ...services.telehealth import ZOOM
from ...services.token_encryption import TokenEncryptionError, decrypt_tokens, encrypt_tokens
from ...utcnow import utc_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PostgresZoomConnectionStore:
    """The Zoom half of ``telehealth_connections``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str) -> ZoomGrant | None:
        row = self._session.get(TelehealthConnectionRow, (user_id, ZOOM))
        if row is None:
            return None
        try:
            secrets = decrypt_tokens(row.encrypted_tokens)
        except TokenEncryptionError:
            # A grant this deployment's key cannot open is a grant nobody can
            # use. Treated as not connected so the clinician is offered a
            # reconnect rather than a provider that fails at booking time.
            logger.warning("telehealth_grant_unreadable provider=%s", ZOOM)
            return None
        return ZoomGrant(
            access_token=secrets.get("access_token", ""),
            refresh_token=secrets.get("refresh_token", ""),
            expires_at=datetime.fromisoformat(secrets["expires_at"]),
            account_handle=row.account_handle,
        )

    def save(self, user_id: str, grant: ZoomGrant) -> None:
        row = self._session.get(TelehealthConnectionRow, (user_id, ZOOM))
        if row is None:
            row = TelehealthConnectionRow(user_id=user_id, provider=ZOOM, connected_at=utc_now())
            self._session.add(row)
        row.encrypted_tokens = encrypt_tokens(
            {
                "access_token": grant.access_token,
                "refresh_token": grant.refresh_token,
                "expires_at": grant.expires_at.isoformat(),
            }
        )
        if grant.account_handle is not None:
            row.account_handle = grant.account_handle
        row.last_error = None
        self._session.flush()

    def delete(self, user_id: str) -> bool:
        row = self._session.get(TelehealthConnectionRow, (user_id, ZOOM))
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True
