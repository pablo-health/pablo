# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request and response shapes for the instrument catalogue and its licences.

One response model serves two screens, because both are asking the same
question about the same list. The settings screen shows every instrument
whose use is restricted, what the restriction is, and whether this practice
has recorded permission. The form builder shows the ones a form may ask,
greying out any the practice has not licensed yet. Splitting them into two
endpoints would mean two lists that could disagree about which instruments
exist.

``attested`` is the field both turn on, and it is the server's answer rather
than something the screen derives: the same field is what the publisher
checks, and a second opinion computed in the browser would be free to drift
from the one that actually refuses a publish.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttestInstrumentRequest(BaseModel):
    """``POST /api/intake/instruments/{code}/attestation``.

    Both fields optional: plenty of these permissions are a licence the
    practice holds rather than something it bought, and there is nothing to
    quote.
    """

    model_config = ConfigDict(extra="forbid")

    license_reference: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class InstrumentResponse(BaseModel):
    """One instrument, and what this practice may do with it.

    ``can_ask_on_a_form`` is whether the engine carries this instrument's
    wording at all — separate from ``attested``, which is whether the
    practice holds permission. An instrument can be licensed and still not
    be askable, which is what a practice sees for one it may use and whose
    items are not in the engine.
    """

    code: str
    display_name: str
    rights: str
    rights_note: str
    publisher_url: str | None
    item_count: int
    can_ask_on_a_form: bool
    attested: bool
    attested_at: datetime | None
    license_reference: str | None


class InstrumentAttestationResponse(BaseModel):
    """One recorded permission, as the practice that recorded it sees it."""

    id: str
    instrument_code: str
    attested_at: datetime
    license_reference: str | None
    notes: str | None
    revoked_at: datetime | None


__all__ = [
    "AttestInstrumentRequest",
    "InstrumentAttestationResponse",
    "InstrumentResponse",
]
