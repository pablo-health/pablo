# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading and writing the practice's billing identity.

The row a claim gets filed as: legal name, tax id, billing NPI, and address.
Singleton, same shape as ``app.scheduling_engine.services.scheduling_policy``
— one row per practice, pinned by ``CHECK (id = 1)``, and reading an
unconfigured practice returns all-``None`` rather than creating a row.

The tax id is the one field here worth protecting: it is encrypted at rest
with the same AES-256-GCM helper already used for OAuth calendar tokens
(``app.services.token_encryption``), and only its last 4 digits are ever
read back out — decrypting the full value is not something this module
does, because nothing in this change needs to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..db.models import PracticeBillingProfileRow
from .token_encryption import decrypt_tokens, encrypt_tokens

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: The singleton row's fixed primary key.
SINGLETON_ID = 1

#: Fields accepted (minus ``tax_id`` -> the encrypted/last4 pair) on a
#: partial update.
_WRITABLE_FIELDS: frozenset[str] = frozenset(
    {
        "legal_name",
        "tax_id_last4",
        "tax_id_type",
        "billing_npi",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "phone",
        "contact_email",
        "eligibility_auto_check",
    }
)

#: Fields returned to a caller: everything writable plus the clearinghouse's
#: id for the provider record, which only the enrollment flow sets.
_READABLE_FIELDS: frozenset[str] = _WRITABLE_FIELDS | {"clearinghouse_provider_id"}

#: The one field with a default other than "unset": a practice that has never
#: opened billing settings still gets its clients' plans checked at intake.
_DEFAULTS: dict[str, object] = {"eligibility_auto_check": True}


def _empty_profile() -> dict[str, object]:
    return {**dict.fromkeys(_READABLE_FIELDS), **_DEFAULTS}


def eligibility_auto_check_enabled(session: Session) -> bool:
    """Does this practice want an eligibility check run whenever coverage lands?"""
    return bool(load_billing_profile(session)["eligibility_auto_check"])


def load_billing_profile(session: Session) -> dict[str, object]:
    """The practice's stored billing profile, or all-``None`` when unset."""
    row = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    if row is None:
        return _empty_profile()
    return {name: getattr(row, name) for name in _READABLE_FIELDS}


def load_billing_tax_id(session: Session) -> str | None:
    """The practice's full tax id, decrypted, or ``None`` when none is stored.

    The one reader of the encrypted value. A document the practice hands to
    a client for reimbursement has to carry the whole number — an insurer
    cannot pay against the last four digits — so this is read at the moment
    of rendering and goes nowhere else: not into a log, not into an audit
    payload, not into the API's own profile response.
    """
    row = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    if row is None or not row.tax_id_encrypted:
        return None
    return decrypt_tokens(row.tax_id_encrypted).get("tax_id") or None


def update_billing_profile(session: Session, patch: dict[str, object]) -> dict[str, object]:
    """Merge ``patch`` over the current profile and upsert the singleton row.

    ``patch`` may carry a raw ``tax_id`` (from the API's write-only field)
    instead of ``tax_id_encrypted`` / ``tax_id_last4`` directly — it is
    encrypted here, and only its last 4 digits are kept in the clear.
    Partial by design: an unmentioned field keeps its current value. Does
    not commit — the caller owns the transaction.
    """
    row = session.get(PracticeBillingProfileRow, SINGLETON_ID)
    now = datetime.now(UTC)

    fields = {k: v for k, v in patch.items() if k in _WRITABLE_FIELDS}

    raw_tax_id = patch.get("tax_id")
    if isinstance(raw_tax_id, str) and raw_tax_id:
        fields["tax_id_last4"] = raw_tax_id[-4:]
        tax_id_encrypted = encrypt_tokens({"tax_id": raw_tax_id})
    else:
        tax_id_encrypted = None

    if row is None:
        row = PracticeBillingProfileRow(id=SINGLETON_ID, created_at=now, updated_at=now)
        session.add(row)

    for key, value in fields.items():
        setattr(row, key, value)
    if tax_id_encrypted is not None:
        row.tax_id_encrypted = tax_id_encrypted
    row.updated_at = now

    session.flush()
    return {name: getattr(row, name) for name in _READABLE_FIELDS}
