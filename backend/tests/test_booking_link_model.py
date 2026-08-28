# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``require_email_confirmation`` is database-enforced, not API-surfaced.

The column must be born ``true`` at the Postgres layer (a ``server_default``,
never a Python-side ``default``) so an INSERT that omits it still lands
``true`` — and it must be absent from every schema a caller can shape a
request or response with, so there is no way to ask for anything else.
"""

from __future__ import annotations

from app.db.platform_models import BookingLinkRow
from app.models.booking_link import (
    BookingLinkResponse,
    CreateBookingLinkRequest,
    PublicBookingLinkResponse,
    UpdateBookingLinkRequest,
)


def test_require_email_confirmation_default_is_server_side() -> None:
    column = BookingLinkRow.__table__.c.require_email_confirmation
    assert column.server_default is not None
    assert column.default is None
    assert column.nullable is False


def test_require_email_confirmation_absent_from_api_schemas() -> None:
    for schema in (
        CreateBookingLinkRequest,
        UpdateBookingLinkRequest,
        BookingLinkResponse,
        PublicBookingLinkResponse,
    ):
        assert "require_email_confirmation" not in schema.model_fields
