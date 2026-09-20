# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake packet service — the rules a stored form has to obey.

The repository knows how to read and write three tables. This is what knows
a form from a pile of rows, and it exists for one rule above all: **a
published version never changes again.**

That rule is what makes an answered form readable. A submission records
which version it answered, so the questions a patient was asked can always
be reconstructed — but only if nobody edited them afterwards. Editing a
published version would not lose the answers; it would quietly change what
they were answers to, which is worse, because nothing about the record would
look wrong.

So the freeze is enforced in exactly one place, and every write path goes
through it. :meth:`IntakePacketService.replace_items` refuses a published
version, and there is no other method that writes an item. A practice that
wants to change a published form gets a new version built from the old one's
items, which is what :meth:`create_version` does.

Publishing validates the whole list rather than each item as it arrives,
because two of the rules are about the list: keys have to be unique across
it, and a visibility rule may only point backwards within it. A draft is
allowed to be half-built; publishing is where it has to make sense.

Publishing is also the one moment a use-restricted measure is checked
against what the practice is licensed to ask. That is why withdrawing a
licence leaves every form already published still working: the check
happened when the version was frozen, and reaching back into a frozen
version is the thing this whole module exists to prevent.

Publishing is also where a consent item stops naming a document and starts
naming one revision of it. The practice picks the document; the revision it
gets pinned to is whichever was live at the moment the form was frozen, so
the practice can keep revising the document afterwards without touching a
form that has already gone out.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from ..intake.items import (
    DISPLAY_ONLY_ITEM_TYPES,
    ConsentDocumentConfig,
    ItemConfigError,
    ItemDraft,
    stored_config,
    validate_item_list,
)
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..intake.items import InstrumentAttested, ItemConfig, PublishedDocumentLookup
    from ..repositories.intake_packet import IntakePacketRepository


class PublishedVersionError(RuntimeError):
    """An attempt to change a version that has already been published."""


def _blank_to_none(value: str | None) -> str | None:
    """A field the editor sent empty, stored as unset."""
    return value if value is not None and value.strip() else None


def _optional_str(value: object) -> str | None:
    """A nullable text column read back off a row."""
    return value if isinstance(value, str) else None


class IntakePacketService:
    """Templates, versions and items, with the freeze enforced."""

    def __init__(
        self,
        repo: IntakePacketRepository,
        published_document: PublishedDocumentLookup | None = None,
        instrument_attested: InstrumentAttested | None = None,
    ) -> None:
        self._repo = repo
        self._published_document = published_document
        self._instrument_attested = instrument_attested

    # --- templates ---

    def create_template(self, name: str, created_by: str) -> dict[str, object]:
        """A new, empty template with a draft first version.

        The version comes with it rather than on a second call: a template
        with no version is a name with nothing behind it, and every caller
        would immediately have to make one.
        """
        now = utc_now()
        template = self._repo.add_template(
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "created_by": created_by,
                "created_at": now,
                "archived_at": None,
            }
        )
        self._repo.add_version(
            {
                "id": str(uuid.uuid4()),
                "template_id": template["id"],
                "version": 1,
                "published_at": None,
                "published_by": None,
                "created_at": now,
            }
        )
        return template

    def list_templates(self, *, include_archived: bool = False) -> list[dict[str, object]]:
        return self._repo.list_templates(include_archived=include_archived)

    def get_template(self, template_id: str) -> dict[str, object] | None:
        return self._repo.get_template(template_id)

    def rename_template(self, template_id: str, name: str) -> dict[str, object] | None:
        """Rename a template, including a published one.

        The name is the practice's label for the form, not part of what a
        patient answered, so renaming is not an edit to a frozen version.
        """
        return self._repo.update_template(template_id, name=name)

    def set_archived(self, template_id: str, archived: bool) -> dict[str, object] | None:
        """Take a template out of circulation, or put it back.

        Never a delete. A version somebody filled in has to stay readable
        for as long as their record does.
        """
        if archived:
            return self._repo.update_template(template_id, archived_at=utc_now())
        return self._repo.update_template(template_id, unarchive=True)

    # --- versions ---

    def list_versions(self, template_id: str) -> list[dict[str, object]]:
        return self._repo.list_versions(template_id)

    def get_version(self, version_id: str) -> dict[str, object] | None:
        return self._repo.get_version(version_id)

    def list_items(self, version_id: str) -> list[dict[str, object]]:
        return self._repo.list_items(version_id)

    def create_version(self, template_id: str) -> dict[str, object] | None:
        """Start a new draft, carrying the latest version's items forward.

        This is how a published form is changed: the old version stays
        exactly as it was and the practice edits a copy. An unpublished
        latest version is returned as-is rather than copied — there is
        already a draft to edit, and stacking a second one would leave the
        editor with two.
        """
        if self._repo.get_template(template_id) is None:
            return None

        latest = self._repo.latest_version(template_id)
        if latest is not None and latest["published_at"] is None:
            return latest

        number = int(latest["version"]) + 1 if latest else 1  # type: ignore[call-overload]
        draft = self._repo.add_version(
            {
                "id": str(uuid.uuid4()),
                "template_id": template_id,
                "version": number,
                "published_at": None,
                "published_by": None,
                "created_at": utc_now(),
            }
        )
        if latest is not None:
            carried = [
                {**row, "id": str(uuid.uuid4()), "version_id": draft["id"]}
                for row in self._repo.list_items(str(latest["id"]))
            ]
            self._repo.replace_items(str(draft["id"]), carried)
        return draft

    # --- items ---

    def replace_items(self, version_id: str, items: list[ItemDraft]) -> list[dict[str, object]]:
        """Swap a draft version's whole item list.

        Raises :class:`PublishedVersionError` on a published version. This is
        the only path that writes an item, so that check is the freeze.
        """
        version = self._repo.get_version(version_id)
        if version is None:
            raise LookupError(version_id)
        if version["published_at"] is not None:
            raise PublishedVersionError(version_id)

        rows: list[dict[str, object]] = [
            {
                "id": str(uuid.uuid4()),
                "version_id": version_id,
                "key": item.key,
                "position": position,
                "item_type": item.item_type,
                # A heading or a paragraph collects nothing, so "required"
                # has nothing to be true about. Normalised here rather than
                # refused, because the editor does not offer the toggle and
                # a stored true would only ever be an artifact.
                "required": item.required and item.item_type not in DISPLAY_ONLY_ITEM_TYPES,
                # An empty box and an unwritten question are the same thing,
                # so both store NULL. Otherwise a label the practice cleared
                # would read as wording of zero characters at publish.
                "label": _blank_to_none(item.label),
                "help_text": _blank_to_none(item.help_text),
                "config": item.config,
                "resign_on_new_version": item.resign_on_new_version,
            }
            for position, item in enumerate(items)
        ]
        return self._repo.replace_items(version_id, rows)

    def publish(self, version_id: str, published_by: str) -> dict[str, object]:
        """Validate a draft's items, pin its documents, and freeze it.

        Raises :class:`ItemConfigError` naming the item that is wrong, or
        :class:`PublishedVersionError` if the version is already frozen.
        Nothing is written unless every item passes, so a refused publish
        leaves the draft exactly as it was.

        **Pinning happens between the validation and the freeze**, which is
        the only window it can. A consent item stores the document a
        practice picked; what a patient signs has to be one exact revision
        of that document, and the revision to use is whichever was current
        the moment the form went live. Writing it afterwards would be an
        edit to a frozen version; writing it earlier would leave a draft
        carrying a pin that goes stale every time the document is revised.
        """
        version = self._repo.get_version(version_id)
        if version is None:
            raise LookupError(version_id)
        if version["published_at"] is not None:
            raise PublishedVersionError(version_id)

        rows = self._repo.list_items(version_id)
        configs = validate_item_list(
            [
                ItemDraft(
                    key=str(row["key"]),
                    item_type=str(row["item_type"]),
                    required=bool(row["required"]),
                    resign_on_new_version=bool(row["resign_on_new_version"]),
                    label=_optional_str(row.get("label")),
                    help_text=_optional_str(row.get("help_text")),
                    config=stored_config(row["config"]),
                )
                for row in rows
            ],
            published_document=self._published_document,
            instrument_attested=self._instrument_attested,
        )
        self._pin_documents(rows, configs)

        published = self._repo.mark_published(version_id, utc_now(), published_by)
        if published is None:  # pragma: no cover — read above proves it exists
            raise LookupError(version_id)
        return published

    def _pin_documents(self, rows: list[dict[str, object]], configs: list[ItemConfig]) -> None:
        """Record on each consent item which revision of its document is live.

        ``configs`` came from ``rows`` in order, so the two zip. A service
        built without a document lookup writes nothing and validates
        nothing about documents — it is the shape a caller with no document
        store gets, and the route that publishes always supplies one.
        """
        if self._published_document is None:
            return
        for row, config in zip(rows, configs, strict=True):
            if not isinstance(config, ConsentDocumentConfig):
                continue
            version_id = self._published_document(config.document_key)
            if version_id is None:  # pragma: no cover — validation refused this already
                continue
            self._repo.set_item_config(
                str(row["id"]),
                {
                    **stored_config(row["config"]),
                    "document_version_id": version_id,
                },
            )


__all__ = ["IntakePacketService", "ItemConfigError", "PublishedVersionError"]
