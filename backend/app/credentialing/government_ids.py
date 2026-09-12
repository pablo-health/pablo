# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The one path to the clinician's encrypted identifiers.

SSN, date of birth, tax id and bank numbers are the most sensitive fields in
the schema. They are encrypted at rest with the AES-256-GCM helper the calendar
tokens and the practice's billing tax id already use, and this module is their
only reader — which is what makes "every decryption is audited" a property of
the code rather than a convention.

Two things callers get for free and must not route around:

* :func:`view_identifiers` writes an audit row naming the fields it decrypted.
  There is no unaudited read. The private helpers that decrypt take an audit
  service, not an optional one.
* Nothing here returns a decrypted value through a log, an exception message or
  a ``repr``. :class:`Identifiers` is the carrier, and it is deliberately not a
  dataclass with a default ``repr``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from ..db.models import CredentialGovernmentIdRow
from ..models.audit import AuditAction, ResourceType
from ..services.token_encryption import decrypt_tokens, encrypt_tokens

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.orm import Session

    from ..models.user import User
    from ..services.audit_service import AuditService


#: Fields a caller may write, and the encrypted column each lands in. Keys are
#: what the API speaks; the ``_encrypted`` suffix never crosses that boundary.
_ENCRYPTED_FIELDS: dict[str, str] = {
    "ssn": "ssn_encrypted",
    "dob": "dob_encrypted",
    "tax_id": "tax_id_encrypted",
}

#: Which encrypted fields keep a last-four in the clear. ``dob`` does not: a
#: partial date of birth is either the whole fact or useless.
_LAST4_FIELDS: frozenset[str] = frozenset({"ssn", "tax_id"})

#: Writable without encryption. The checklist's scalar answers land here too —
#: not because they are sensitive, but because this row has one writer and a
#: second one would race it.
_PLAIN_FIELDS: frozenset[str] = frozenset(
    {
        "tax_id_type",
        "type2_npi",
        "business_structure",
        "sole_proprietor",
        "supervision_status",
        "caqh_id",
        "medicare_intent",
        "medicaid_intent",
    }
)


class Identifiers:
    """A decrypted read. Never logged, never serialised by default.

    ``__repr__`` is overridden rather than inherited so that an exception
    traceback, a debugger's locals dump or a stray ``%s`` cannot put an SSN in
    front of anyone. The values are reachable only by naming the attribute.
    """

    __slots__ = ("dob", "ssn", "tax_id")

    def __init__(
        self, ssn: str | None = None, dob: date | None = None, tax_id: str | None = None
    ) -> None:
        self.ssn = ssn
        self.dob = dob
        self.tax_id = tax_id

    def __repr__(self) -> str:
        present = [name for name in self.__slots__ if getattr(self, name) is not None]
        return f"<Identifiers present={sorted(present)}>"


def load_summary(session: Session, user_id: str) -> dict[str, object]:
    """What a form may show without decrypting anything.

    The last-four digits, the entity facts, and a boolean per encrypted field
    saying whether one is on file. Safe to return from an endpoint and safe to
    put in a response body; it discloses which numbers exist, not what they are.
    """
    row = session.get(CredentialGovernmentIdRow, user_id)
    if row is None:
        return {
            "has_ssn": False,
            "has_dob": False,
            "has_tax_id": False,
            "ssn_last4": None,
            "tax_id_last4": None,
            "tax_id_type": None,
            "type2_npi": None,
            "business_structure": None,
            "sole_proprietor": None,
            "supervision_status": None,
            "caqh_id": None,
            "medicare_intent": None,
            "medicaid_intent": None,
        }
    return {
        "has_ssn": bool(row.ssn_encrypted),
        "has_dob": bool(row.dob_encrypted),
        "has_tax_id": bool(row.tax_id_encrypted),
        "ssn_last4": row.ssn_last4,
        "tax_id_last4": row.tax_id_last4,
        "tax_id_type": row.tax_id_type,
        "type2_npi": row.type2_npi,
        "business_structure": row.business_structure,
        "sole_proprietor": row.sole_proprietor,
        "supervision_status": row.supervision_status,
        "caqh_id": row.caqh_id,
        "medicare_intent": row.medicare_intent,
        "medicaid_intent": row.medicaid_intent,
    }


def view_identifiers(
    session: Session,
    user: User,
    audit: AuditService,
    fields: list[str],
    request: Request | None = None,
) -> Identifiers:
    """Decrypt the named fields, recording the read.

    ``fields`` is explicit rather than "everything on the row" so that filling
    a CAQH section that wants a date of birth does not also decrypt an SSN, and
    so the audit row says which of them was actually disclosed. A caller asking
    for a field with nothing stored gets ``None`` for it and no audit entry for
    that name — the log should record disclosures, not intentions.

    Raises ``ValueError`` on an unknown field name, because a typo would
    otherwise read as "nothing on file" and hide a bug behind a plausible
    answer.
    """
    unknown = sorted(set(fields) - set(_ENCRYPTED_FIELDS))
    if unknown:
        msg = f"Unknown credential identifier field(s): {unknown}"
        raise ValueError(msg)

    row = session.get(CredentialGovernmentIdRow, user.id)
    if row is None:
        return Identifiers()

    out = Identifiers()
    disclosed: list[str] = []
    for name in fields:
        blob = getattr(row, _ENCRYPTED_FIELDS[name])
        if not blob:
            continue
        raw = decrypt_tokens(blob).get(name)
        if raw is None:
            continue
        disclosed.append(name)
        if name == "dob":
            out.dob = date.fromisoformat(raw)
        else:
            setattr(out, name, raw)

    if disclosed:
        # ``changes`` carries field NAMES, never values — and the names are
        # passed under ``decrypted_fields`` rather than as keys, because
        # ``ssn`` and ``dob`` are themselves in PHI_FIELD_NAMES and a key of
        # that name would be refused.
        audit.log(
            AuditAction.CREDENTIAL_IDENTIFIERS_VIEWED,
            user=user,
            request=request,
            resource_type=ResourceType.CREDENTIAL_RECORD,
            resource_id=user.id,
            changes={"decrypted_fields": sorted(disclosed)},
        )
    return out


def update_identifiers(
    session: Session,
    user: User,
    audit: AuditService,
    patch: dict[str, object],
    request: Request | None = None,
) -> dict[str, object]:
    """Encrypt and store the named identifiers; upsert the row.

    Partial: an unmentioned field keeps its current value, and passing ``None``
    for one CLEARS it (both the ciphertext and its last-four), which is how a
    clinician removes an SSN she should not have been asked for. Does not
    commit — the caller owns the transaction.

    ``dob`` is accepted as a ``date`` and stored as an ISO string inside the
    ciphertext, so the encrypted blob carries no timezone ambiguity and reads
    back as the civil date it was.
    """
    unknown = sorted(set(patch) - set(_ENCRYPTED_FIELDS) - _PLAIN_FIELDS)
    if unknown:
        msg = f"Unknown credential identifier field(s): {unknown}"
        raise ValueError(msg)

    now = datetime.now(UTC)
    row = session.get(CredentialGovernmentIdRow, user.id)
    if row is None:
        row = CredentialGovernmentIdRow(user_id=user.id, created_at=now, updated_at=now)
        session.add(row)

    for name, column in _ENCRYPTED_FIELDS.items():
        if name not in patch:
            continue
        value = patch[name]
        if value is None or value == "":
            setattr(row, column, None)
            if name in _LAST4_FIELDS:
                setattr(row, f"{name}_last4", None)
            continue
        plain = value.isoformat() if isinstance(value, date) else str(value)
        setattr(row, column, encrypt_tokens({name: plain}))
        if name in _LAST4_FIELDS:
            setattr(row, f"{name}_last4", plain[-4:])

    for name in _PLAIN_FIELDS & set(patch):
        setattr(row, name, patch[name])

    row.updated_at = now
    session.flush()

    audit.log(
        AuditAction.CREDENTIAL_IDENTIFIERS_UPDATED,
        user=user,
        request=request,
        resource_type=ResourceType.CREDENTIAL_RECORD,
        resource_id=user.id,
        changes={"changed_fields": sorted(patch)},
    )
    return load_summary(session, user.id)
