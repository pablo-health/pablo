# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake document service — the rules a stored consent document obeys.

The repository knows how to read and write one table. This is what knows a
document from a pile of versions, and it exists for one rule above all: **a
published version never changes again.**

That rule is what makes a signature mean anything. A signature records the
digest of the text that was agreed to, so the text has to still be there,
unchanged, when somebody asks years later what that was. Editing a
published version would not lose the signature; it would quietly change
what was signed, which is worse, because nothing in the record would look
wrong.

So the freeze is enforced in exactly one place.
:meth:`IntakeDocumentService.update_draft` refuses a published version, and
there is no other method that rewrites text. A practice that wants to
change a published document gets a new version carrying the old one's text,
which is what :meth:`new_version` does.

**The digest is maintained on every write, not computed at publish.** It is
a pure function of the body, so a draft carrying a stale one would be a
second place for the truth to live. Publishing does not compute it; it
freezes the row the digest already describes.

A document's versions share a ``document_key``, and that is what a form
points at. A form says "ask them to sign THIS document"; which version they
actually sign is decided when the form is published, so a practice can
revise a document without editing every form that references it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..intake.documents import content_digest
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.intake_document import IntakeDocumentRepository

#: Who a document can require a signature from. A guardian signs alongside
#: the patient rather than instead of them, so this is a set of roles that
#: must all sign rather than a choice between them.
SIGNER_ROLES: tuple[str, ...] = ("patient", "guardian")


class PublishedDocumentError(RuntimeError):
    """An attempt to change a version that has already been published."""


class SignerRoleError(ValueError):
    """A signer role that is not one a document can ask for."""


class IntakeDocumentService:
    """Consent documents and their versions, with the freeze enforced."""

    def __init__(self, repo: IntakeDocumentRepository) -> None:
        self._repo = repo

    # --- reads ---

    def list_documents(self) -> list[dict[str, object]]:
        """The newest version of every document the practice has written."""
        return self._repo.latest_per_key()

    def list_published(self) -> list[dict[str, object]]:
        """The newest published version of every document.

        What the form editor offers: a document nobody can sign yet is not
        one a practice should be able to put on a form.
        """
        return [
            published
            for row in self._repo.latest_per_key()
            if (published := self._repo.published_for_key(str(row["document_key"]))) is not None
        ]

    def get(self, document_id: str) -> dict[str, object] | None:
        return self._repo.get(document_id)

    def versions(self, document_key: str) -> list[dict[str, object]]:
        return self._repo.versions_for_key(document_key)

    def published_for_key(self, document_key: str) -> dict[str, object] | None:
        return self._repo.published_for_key(document_key)

    # --- writes ---

    def create(
        self,
        *,
        title: str,
        body_markdown: str,
        requires_signature: bool = True,
        signer_roles: list[str] | None = None,
    ) -> dict[str, object]:
        """A new document, as an unpublished version 1."""
        roles = _checked_roles(signer_roles)
        return self._repo.add(
            {
                "id": str(uuid.uuid4()),
                "document_key": str(uuid.uuid4()),
                "title": title,
                "body_markdown": body_markdown,
                "version": 1,
                "digest": content_digest(body_markdown),
                "published_at": None,
                "published_by": None,
                "requires_signature": requires_signature,
                "signer_roles": roles,
                "created_at": utc_now(),
            }
        )

    def update_draft(
        self, document_id: str, *, title: str | None = None, body_markdown: str | None = None
    ) -> dict[str, object]:
        """Rewrite a draft's title or text.

        Raises :class:`PublishedDocumentError` on a published version. This
        is the only path that rewrites text, so that check is the freeze.
        """
        row = self._require(document_id)
        if row["published_at"] is not None:
            raise PublishedDocumentError(document_id)

        body = body_markdown if body_markdown is not None else str(row["body_markdown"])
        updated = self._repo.update_draft(
            document_id,
            title=title if title is not None else str(row["title"]),
            body_markdown=body,
            digest=content_digest(body),
        )
        if updated is None:  # pragma: no cover — the read above proves it exists
            raise LookupError(document_id)
        return updated

    def publish(self, document_id: str, published_by: str) -> dict[str, object]:
        """Freeze this version and let forms start pointing at it.

        Raises :class:`PublishedDocumentError` if it is already frozen. The
        digest is not recomputed here: it already describes this text, and
        recomputing it at the moment of freezing would make publishing the
        one write that could change it.
        """
        row = self._require(document_id)
        if row["published_at"] is not None:
            raise PublishedDocumentError(document_id)

        published = self._repo.mark_published(document_id, utc_now(), published_by)
        if published is None:  # pragma: no cover — the read above proves it exists
            raise LookupError(document_id)
        return published

    def new_version(self, document_id: str) -> dict[str, object]:
        """Start a draft carrying the published text forward.

        This is how a published document is changed: the old version stays
        exactly as it was, and the practice edits a copy. A document that
        already has a draft hands that one back rather than stacking a
        second — the database refuses two, and the practice can reach this
        from more than one screen. A document whose only version is still a
        draft is that case: the draft it already has is the thing to edit.
        """
        row = self._require(document_id)
        key = str(row["document_key"])

        latest = self._repo.latest_for_key(key)
        if latest is not None and latest["published_at"] is None:
            return latest
        if latest is None:  # pragma: no cover — the read above proves one exists
            raise LookupError(document_id)

        return self._repo.add(
            {
                "id": str(uuid.uuid4()),
                "document_key": key,
                "title": latest["title"],
                "body_markdown": latest["body_markdown"],
                "version": int(latest["version"]) + 1,  # type: ignore[call-overload]
                "digest": latest["digest"],
                "published_at": None,
                "published_by": None,
                "requires_signature": latest["requires_signature"],
                "signer_roles": latest["signer_roles"],
                "created_at": utc_now(),
            }
        )

    def _require(self, document_id: str) -> dict[str, object]:
        row = self._repo.get(document_id)
        if row is None:
            raise LookupError(document_id)
        return row


def _checked_roles(signer_roles: list[str] | None) -> list[str]:
    """The roles a document asks to sign, defaulted and de-duplicated.

    In the order :data:`SIGNER_ROLES` names them rather than the order they
    arrived, so two documents asking for the same signatures store the same
    list and read the same on the screen.
    """
    if signer_roles is None:
        return ["patient"]
    unknown = sorted(set(signer_roles) - set(SIGNER_ROLES))
    if unknown:
        raise SignerRoleError(f"{unknown[0]!r} is not somebody a document can ask to sign")
    if not signer_roles:
        raise SignerRoleError("A document needs at least one person to sign it")
    return [role for role in SIGNER_ROLES if role in signer_roles]


__all__ = [
    "SIGNER_ROLES",
    "IntakeDocumentService",
    "PublishedDocumentError",
    "SignerRoleError",
]
