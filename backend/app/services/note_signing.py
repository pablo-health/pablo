# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Signing, locking, unlocking and adding to a clinical note.

The rules, so they live in one place:

* **Sign and lock.** Signing records the signer's name and credentials as
  entered, the signing user and the server time, together with a snapshot of
  the body as signed (a :class:`~app.models.note_signing.NoteSignature`), and
  stamps ``finalized_at`` in the same transaction. A finalized note is locked:
  edits are refused with 409.
* **Addenda add; they never correct.** An addendum is appended to a locked
  note with its own signature and chained onto the previous one, so removing
  or reordering any of them is detectable. The note body is never touched.
* **Unlock corrects.** Unlocking requires a reason in the clinician's own
  words, keeps the signed version (body, signature and all) as a superseded
  version with that reason, and returns the note to editable. Signing again
  makes the next version. Nothing signed is ever overwritten.

Finalized notes from before signatures existed carry no version. They stay
locked, take addenda, and can be signed going forward.

The digests reuse the prescribing integrity primitives
(:mod:`app.prescribing.integrity`): a canonical content digest, and a chain
digest linking each addendum to the one before it.
"""

from __future__ import annotations

from datetime import UTC
from typing import TYPE_CHECKING, Any

from ..api_errors import BadRequestError, ConflictError
from ..prescribing.integrity import chain_digest, content_digest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from ..models import Note
    from ..models.note_signing import NoteAddendum, NoteSignature


class NoteLockedError(ConflictError):
    """Raised when a locked (finalized) note is edited in place."""

    code = "NOTE_LOCKED"


class NoteAlreadySignedError(ConflictError):
    """Raised when signing a note that already carries a current signature."""

    code = "NOTE_ALREADY_SIGNED"


class NoteNotLockedError(ConflictError):
    """Raised when an addendum or unlock is attempted on an unlocked note."""

    code = "NOTE_NOT_LOCKED"


class NoteNotSignedError(ConflictError):
    """Raised when unlocking a finalized note that has no signed version to keep."""

    code = "NOTE_NOT_SIGNED"


class SignatureNameRequiredError(BadRequestError):
    """Raised when a signature is attempted with a blank name."""

    code = "SIGNER_NAME_REQUIRED"


class UnlockReasonRequiredError(BadRequestError):
    """Raised when an unlock is attempted without a reason."""

    code = "UNLOCK_REASON_REQUIRED"


class AddendumTextRequiredError(BadRequestError):
    """Raised when an addendum is attempted with no text."""

    code = "ADDENDUM_TEXT_REQUIRED"


def clean_signer(name: str, credentials: str | None) -> tuple[str, str | None]:
    """Strip the signer's name and credentials; refuse a blank name."""
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise SignatureNameRequiredError("A signature needs a name")
    cleaned_credentials = (credentials or "").strip() or None
    return cleaned_name, cleaned_credentials


def current_signature(note: Note, signatures: Sequence[NoteSignature]) -> NoteSignature | None:
    """The signature the note stands on now, if any.

    The latest version that has not been unlocked — and only while the note
    is finalized, since unlocking is what clears ``finalized_at``.
    """
    if note.finalized_at is None or not signatures:
        return None
    latest = signatures[-1]
    return latest if latest.unlocked_at is None else None


def _instant(moment: datetime) -> str:
    """A timestamp as one canonical string, whatever zone the driver handed back."""
    return moment.astimezone(UTC).isoformat()


def signature_snapshot(signature: NoteSignature) -> dict[str, Any]:
    """The canonical snapshot a signed version's ``digest`` commits to."""
    return {
        "note_id": signature.note_id,
        "version": signature.version,
        "note_type": signature.note_type,
        "note_type_version": signature.note_type_version,
        "content": signature.content,
        "content_edited": signature.content_edited,
        "signed_by": signature.signed_by,
        "signer_name": signature.signer_name,
        "signer_credentials": signature.signer_credentials,
        "signed_at": _instant(signature.signed_at),
    }


def signature_digest(signature: NoteSignature) -> str:
    """The digest a signed version is stored with."""
    return content_digest(signature_snapshot(signature))


def verify_signature(signature: NoteSignature) -> bool:
    """Whether a signed version still matches the digest it was signed with."""
    return signature_digest(signature) == signature.digest


def _addendum_content(
    note_id: str,
    *,
    text: str,
    signer_name: str,
    signer_credentials: str | None,
    author: str,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "note_id": note_id,
        "text": text,
        "signer_name": signer_name,
        "signer_credentials": signer_credentials,
        "created_by": author,
        "created_at": _instant(created_at),
    }


def addendum_chain_link(
    note_id: str,
    previous: str | None,
    *,
    text: str,
    signer_name: str,
    signer_credentials: str | None,
    author: str,
    created_at: datetime,
) -> str:
    """The chain digest of an addendum appended after ``previous``."""
    entry = content_digest(
        _addendum_content(
            note_id,
            text=text,
            signer_name=signer_name,
            signer_credentials=signer_credentials,
            author=author,
            created_at=created_at,
        )
    )
    return chain_digest(previous, entry)


def verify_addenda_chain(addenda: Sequence[NoteAddendum]) -> bool:
    """Recompute the addendum chain and confirm every link is intact.

    ``addenda`` must be in chain order (oldest first). Any after-the-fact
    edit, removal or reordering makes this ``False``; no addenda is intact.
    """
    previous: str | None = None
    for entry in addenda:
        if entry.prev_digest != previous:
            return False
        expected = addendum_chain_link(
            entry.note_id,
            previous,
            text=entry.text,
            signer_name=entry.signer_name,
            signer_credentials=entry.signer_credentials,
            author=entry.created_by,
            created_at=entry.created_at,
        )
        if entry.digest != expected:
            return False
        previous = entry.digest
    return True
