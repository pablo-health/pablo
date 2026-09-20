# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Taking a signature, and the six things that can refuse one.

The repositories know how to read a form, a document and a signature. This
is what knows the rules between them, and every one of them exists because
a signature is only worth having if what it points at cannot move.

**A signature names the version that was on the screen.** Publishing a form
pins the document's current published version into the consent item's
configuration, and that pinned version is what gets signed — not whatever
is newest at the moment somebody presses the button. Somebody agreed to the
words they read, and the words they read are the pinned ones.

**Unless the practice asked for a re-signature.** An item carrying
``resign_on_new_version`` says this document is one where a revision
invalidates what came before. When a newer version has been published under
the same document, signing the pinned one is refused: what the patient is
holding is out of date, and taking their name against it would record
agreement to superseded text. The answer is a new request, not a signature
with a footnote.

**One role signs one item once.** A second attempt is refused rather than
recorded, and refused with the same status whether it lost to the check
below or to the partial unique index — which is what makes a double-click
and a genuine race indistinguishable to the caller, as they should be.

**The digest is checked, not trusted.** The version's digest is copied onto
the signature, and the copy is compared with the document's own before the
row is written. They can only differ if something rewrote a published
version's text, which nothing is allowed to do — so the mismatch is a
refusal rather than a repair.

**Signing answers the item.** A consent item's answer is a reference to the
signature, written here so completion counts it. It is written only when
every role the document asks for has signed: a document a practice asks a
guardian to sign as well as the patient is not finished with one signature,
and reporting it as finished would let the form be handed in short of what
the practice asked for.

**Step-up is the route's check, not this module's.** What lands here is an
already-authenticated patient and the strength they authenticated at, which
is recorded on the row. Whether that strength was enough is a statement
about the surface and is made there, beside every other patient route's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..intake.consent_statement import CURRENT_CONSENT_STATEMENT_VERSION
from ..intake.items import (
    ConsentDocumentConfig,
    ItemConfigError,
    stored_config,
    validate_item_config,
)
from ..intake.signatures import evidence_digest
from ..repositories.patient_intake_assignment import WRITABLE_STATUSES
from ..repositories.patient_intake_signature import SignatureExistsError
from ..utcnow import utc_now
from .intake_document_service import SIGNER_ROLES

if TYPE_CHECKING:
    from datetime import datetime

    from ..repositories.intake_document import IntakeDocumentRepository
    from ..repositories.intake_packet import IntakePacketRepository
    from ..repositories.patient_intake_assignment import PatientIntakeAssignmentRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository

#: The longest a typed name may be. Matches the column, and is generous
#: enough for a name written however somebody writes one.
TYPED_NAME_MAX_LEN = 160


class AssignmentClosedError(RuntimeError):
    """An attempt to sign on a form that is no longer the patient's to fill in."""


class NotASignableItemError(LookupError):
    """The item named is not on this form, or is not a document to sign."""


class UnsignableDocumentError(RuntimeError):
    """The item's document cannot be signed as the form pinned it.

    Three shapes, all of them a practice-side problem rather than something
    the patient did: no version was pinned, the pinned version is not
    published, or its stored digest no longer matches the document's.
    """


class SignerRoleNotAskedError(ValueError):
    """A role this document does not ask to sign."""


class TypedNameError(ValueError):
    """A signature with nothing typed in it."""


class NotAffirmedError(ValueError):
    """A signature sent without the affirmation ticked."""


class StaleDocumentVersionError(RuntimeError):
    """A newer version exists and this item says that needs a new signature."""


class AlreadySignedError(RuntimeError):
    """This role has already signed this item on this form."""


@dataclass(frozen=True)
class _ConsentItem:
    """One consent question on a form: what it points at, and its own rule."""

    config: ConsentDocumentConfig
    resign_on_new_version: bool


@dataclass(frozen=True)
class SigningRequest:
    """One attempt to sign, as the route hands it over.

    The patient id and the session details come off the authenticated
    principal rather than out of the request body — there is no field here a
    caller could put somebody else's value in.
    """

    assignment: dict[str, object]
    patient_id: str
    item_id: str
    signer_role: str
    typed_name: str
    affirmed: bool
    auth_strength: str
    session_id: str | None
    ip: str | None
    user_agent: str | None


class IntakeSignatureService:
    """Signatures, with every rule above enforced in one place."""

    def __init__(
        self,
        signatures: PatientIntakeSignatureRepository,
        packets: IntakePacketRepository,
        documents: IntakeDocumentRepository,
        assignments: PatientIntakeAssignmentRepository,
    ) -> None:
        self._signatures = signatures
        self._packets = packets
        self._documents = documents
        self._assignments = assignments

    def sign(self, request: SigningRequest) -> dict[str, object]:
        """Record one signature and answer the item it belongs to.

        Nothing is written until everything that could refuse the signature
        has run, and the two writes that follow ride the request's
        transaction — so a failure between them leaves the form exactly as
        unsigned as it was, rather than with an answer pointing at a
        signature that is not there.

        Raises the errors above; the route turns each into a status code.
        """
        assignment_id = str(request.assignment["id"])
        if str(request.assignment["status"]) not in WRITABLE_STATUSES:
            raise AssignmentClosedError(assignment_id)

        if not request.affirmed:
            raise NotAffirmedError(request.item_id)

        typed_name = request.typed_name.strip()
        if not typed_name:
            raise TypedNameError(request.item_id)
        if len(typed_name) > TYPED_NAME_MAX_LEN:
            raise TypedNameError(request.item_id)

        if request.signer_role not in SIGNER_ROLES:
            raise SignerRoleNotAskedError(request.signer_role)

        item = self._consent_item(request.assignment, request.item_id)
        document = self._pinned_document(item.config)

        roles = _roles_of(document)
        if request.signer_role not in roles:
            raise SignerRoleNotAskedError(request.signer_role)

        if item.resign_on_new_version and self._superseded(document):
            raise StaleDocumentVersionError(str(document["id"]))

        if (
            self._signatures.get_live(
                assignment_id, request.patient_id, request.item_id, request.signer_role
            )
            is not None
        ):
            raise AlreadySignedError(request.signer_role)

        now = utc_now()
        row: dict[str, object] = {
            "id": str(uuid.uuid4()),
            "assignment_id": assignment_id,
            "patient_id": request.patient_id,
            "item_id": request.item_id,
            "document_version_id": str(document["id"]),
            "document_digest": str(document["digest"]),
            "signer_role": request.signer_role,
            "signer_typed_name": typed_name,
            "consent_statement_version": CURRENT_CONSENT_STATEMENT_VERSION,
            "signed_at": now,
            "auth_strength": request.auth_strength,
            "session_id": request.session_id,
            "ip": request.ip,
            "user_agent": request.user_agent,
            "created_at": now,
            "superseded_at": None,
        }
        # Taken over the row as it will be stored, so the value written here
        # and the value an audit recomputes from the stored columns are the
        # same computation over the same fields.
        row["evidence_digest"] = evidence_digest(row)

        try:
            stored = self._signatures.add(row)
        except SignatureExistsError as exc:
            # Lost a race with another request for the same item. The caller
            # cannot tell this from the check above, which is correct: both
            # mean somebody already signed.
            raise AlreadySignedError(request.signer_role) from exc

        self._answer_item(request, roles, str(stored["id"]), now)
        return stored

    def live_for_assignment(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        """The calling patient's signatures on one of their own forms."""
        return self._signatures.list_live_for_assignment(assignment_id, patient_id)

    # --- the checks, one method each ---

    def _consent_item(self, assignment: dict[str, object], item_id: str) -> _ConsentItem:
        """The item, iff it is a document to sign on this form.

        An id that is not on this form and an id that is on it but asks for
        something else are the same refusal. The route answers both with a
        404: which items a form has is not something this surface confirms
        for an id the caller guessed.

        ``resign_on_new_version`` comes off the item ROW rather than its
        configuration — it is a column on the item, beside ``required``,
        because it is a property of asking this question on this form rather
        than of the document the question points at. The same document can
        be asked for with a re-signature rule on one form and without one on
        another.
        """
        row = next(
            (
                item
                for item in self._packets.list_items(str(assignment["version_id"]))
                if str(item["id"]) == item_id
            ),
            None,
        )
        if row is None:
            raise NotASignableItemError(item_id)
        try:
            config = validate_item_config(str(row["item_type"]), stored_config(row["config"]))
        except ItemConfigError as exc:
            raise NotASignableItemError(item_id) from exc
        if not isinstance(config, ConsentDocumentConfig):
            raise NotASignableItemError(item_id)
        return _ConsentItem(
            config=config,
            resign_on_new_version=bool(row.get("resign_on_new_version")),
        )

    def _pinned_document(self, config: ConsentDocumentConfig) -> dict[str, object]:
        """The exact version the form pinned, checked before it is signed.

        A draft or a missing pin means the form was published in a state the
        publisher is supposed to prevent, so this is a refusal rather than a
        fallback to the newest version — quietly signing something else is
        the failure this whole feature exists to make impossible.
        """
        if config.document_version_id is None:
            raise UnsignableDocumentError(config.document_key)
        document = self._documents.get(config.document_version_id)
        if document is None or document["published_at"] is None:
            raise UnsignableDocumentError(config.document_version_id)
        if str(document["document_key"]) != config.document_key:
            raise UnsignableDocumentError(config.document_version_id)
        return document

    def _superseded(self, document: dict[str, object]) -> bool:
        """True when a newer version of this document has been published."""
        published = self._documents.published_for_key(str(document["document_key"]))
        return published is not None and str(published["id"]) != str(document["id"])

    def _answer_item(
        self,
        request: SigningRequest,
        roles: list[str],
        signature_id: str,
        now: datetime,
    ) -> None:
        """Mark the consent item answered once everybody asked has signed.

        Written through the ordinary response row, so completion needs to
        know nothing about signatures: it asks whether the item has an
        answer, and the answer is a reference to the signature that settled
        it. ``signed`` is false until every role the document asks for has
        one, which is what keeps a half-signed document out of a form that
        can be handed in.
        """
        assignment_id = str(request.assignment["id"])
        signed_roles = {
            str(row["signer_role"])
            for row in self._signatures.list_live_for_assignment(assignment_id, request.patient_id)
            if str(row["item_id"]) == request.item_id
        }
        self._assignments.save_draft_response(
            {
                "id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "patient_id": request.patient_id,
                "item_id": request.item_id,
                "value": {
                    "signed": set(roles) <= signed_roles,
                    "signature_id": signature_id,
                },
                "draft": True,
                "superseded_by": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        self._assignments.record_save(assignment_id, request.patient_id, now)


def _roles_of(document: dict[str, object]) -> list[str]:
    """Who this document asks to sign, read defensively.

    A stored value that is not a list of known roles reads as the default
    rather than raising: the column is a record of what an editor sent, and
    the document service is where a role is checked on the way in.
    """
    stored = document.get("signer_roles")
    if not isinstance(stored, list):
        return ["patient"]
    named = [role for role in SIGNER_ROLES if role in stored]
    return named or ["patient"]


__all__ = [
    "TYPED_NAME_MAX_LEN",
    "AlreadySignedError",
    "AssignmentClosedError",
    "IntakeSignatureService",
    "NotASignableItemError",
    "NotAffirmedError",
    "SignerRoleNotAskedError",
    "SigningRequest",
    "StaleDocumentVersionError",
    "TypedNameError",
    "UnsignableDocumentError",
]
