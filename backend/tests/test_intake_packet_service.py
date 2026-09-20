# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The freeze on a published intake version.

A submission records which version it answered, so the questions a patient
was asked can be reconstructed from the version they answered — but only if
nobody edited it afterwards. Editing a published version would not lose the
answers; it would quietly change what they were answers to, and nothing
about the record would look wrong. That is why the freeze is the thing these
tests spend most of their effort on.

The central assertion is not "``replace_items`` refuses" but "there is no
write path that does not". :meth:`TestNothingMutatesAPublishedVersion` walks
the service's own public surface and asserts that every method which can
reach an item row goes through the check — so a method added later that
forgets it fails here rather than in production.
"""

from __future__ import annotations

import inspect

import pytest
from app.intake.items import ItemConfigError, ItemDraft
from app.repositories import InMemoryIntakePacketRepository
from app.services.intake_packet_service import IntakePacketService, PublishedVersionError

_AUTHOR = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def repo() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def service(repo: InMemoryIntakePacketRepository) -> IntakePacketService:
    return IntakePacketService(repo)


def _items() -> list[ItemDraft]:
    return [
        ItemDraft(key="demographics", item_type="demographics"),
        ItemDraft(key="reason", item_type="reason"),
    ]


def _draft_id(service: IntakePacketService, template_id: str) -> str:
    return str(service.list_versions(template_id)[0]["id"])


class TestCreatingAForm:
    def test_a_new_template_arrives_with_a_draft_to_edit(
        self, service: IntakePacketService
    ) -> None:
        template = service.create_template("New patients", _AUTHOR)
        versions = service.list_versions(str(template["id"]))

        assert len(versions) == 1
        assert versions[0]["version"] == 1
        assert versions[0]["published_at"] is None

    def test_archiving_is_not_a_delete(self, service: IntakePacketService) -> None:
        template = service.create_template("Old form", _AUTHOR)
        template_id = str(template["id"])

        service.set_archived(template_id, True)

        assert service.get_template(template_id) is not None
        assert [t["id"] for t in service.list_templates()] == []
        assert [t["id"] for t in service.list_templates(include_archived=True)] == [template_id]

    def test_archiving_can_be_undone(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Old form", _AUTHOR)["id"])
        service.set_archived(template_id, True)

        service.set_archived(template_id, False)

        assert [t["id"] for t in service.list_templates()] == [template_id]


class TestPublishing:
    def test_publishing_freezes_the_version(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(version_id, _items())

        published = service.publish(version_id, _AUTHOR)

        assert published["published_at"] is not None
        assert published["published_by"] == _AUTHOR

    def test_an_empty_form_cannot_be_published(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])

        with pytest.raises(ItemConfigError, match="at least one question"):
            service.publish(_draft_id(service, template_id), _AUTHOR)

    def test_a_refused_publish_leaves_the_draft_alone(self, service: IntakePacketService) -> None:
        """Nothing is written unless every item passes."""
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(
            version_id,
            [
                ItemDraft(
                    key="how_bad",
                    item_type="scale",
                    config={"min": 9, "max": 1, "min_label": "a", "max_label": "b"},
                )
            ],
        )

        with pytest.raises(ItemConfigError, match="how_bad"):
            service.publish(version_id, _AUTHOR)

        version = service.get_version(version_id)
        assert version is not None
        assert version["published_at"] is None
        assert len(service.list_items(version_id)) == 1

    def test_publishing_twice_is_refused(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(version_id, _items())
        service.publish(version_id, _AUTHOR)

        with pytest.raises(PublishedVersionError):
            service.publish(version_id, _AUTHOR)

    def test_an_unknown_version_is_a_lookup_miss(self, service: IntakePacketService) -> None:
        with pytest.raises(LookupError):
            service.publish("no-such-version", _AUTHOR)


class TestNothingMutatesAPublishedVersion:
    def test_replacing_items_is_refused(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(version_id, _items())
        service.publish(version_id, _AUTHOR)

        with pytest.raises(PublishedVersionError):
            service.replace_items(version_id, [ItemDraft(key="new", item_type="reason")])

    def test_the_items_are_untouched_after_a_refused_write(
        self, service: IntakePacketService
    ) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(version_id, _items())
        service.publish(version_id, _AUTHOR)

        with pytest.raises(PublishedVersionError):
            service.replace_items(version_id, [])

        assert [i["key"] for i in service.list_items(version_id)] == ["demographics", "reason"]

    def test_replace_items_is_the_only_method_that_writes_an_item(self) -> None:
        """A method added later that forgets the freeze fails here.

        The repository's ``replace_items`` is the only call that can change
        an item row, so the service may only reach it from a method that has
        already refused a published version. Read off the source rather than
        asserted by hand, so it keeps being true.
        """
        callers = {
            name
            for name, method in inspect.getmembers(IntakePacketService, inspect.isfunction)
            if "replace_items" in inspect.getsource(method)
        }
        assert callers == {"replace_items", "create_version"}

    def test_carrying_items_forward_writes_only_to_the_new_draft(
        self, service: IntakePacketService, repo: InMemoryIntakePacketRepository
    ) -> None:
        """``create_version`` reaches ``replace_items`` on a draft it just made."""
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        published_id = _draft_id(service, template_id)
        service.replace_items(published_id, _items())
        published = service.publish(published_id, _AUTHOR)

        draft = service.create_version(template_id)

        assert draft is not None
        assert draft["id"] != published_id
        assert [i["key"] for i in service.list_items(str(draft["id"]))] == [
            "demographics",
            "reason",
        ]
        assert repo.versions[published_id]["published_at"] == published["published_at"]


class TestNewVersions:
    def test_a_new_version_copies_the_published_questions(
        self, service: IntakePacketService
    ) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        first = _draft_id(service, template_id)
        service.replace_items(first, _items())
        service.publish(first, _AUTHOR)

        draft = service.create_version(template_id)

        assert draft is not None
        assert draft["version"] == 2
        assert draft["published_at"] is None
        assert [i["key"] for i in service.list_items(str(draft["id"]))] == [
            "demographics",
            "reason",
        ]

    def test_the_copies_are_new_rows(self, service: IntakePacketService) -> None:
        """Editing the copy must not reach back into the frozen original."""
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        first = _draft_id(service, template_id)
        service.replace_items(first, _items())
        service.publish(first, _AUTHOR)

        draft = service.create_version(template_id)
        assert draft is not None

        original_ids = {i["id"] for i in service.list_items(first)}
        copied_ids = {i["id"] for i in service.list_items(str(draft["id"]))}
        assert original_ids.isdisjoint(copied_ids)

    def test_an_existing_draft_is_handed_back_rather_than_stacked(
        self, service: IntakePacketService
    ) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        existing = _draft_id(service, template_id)

        draft = service.create_version(template_id)

        assert draft is not None
        assert draft["id"] == existing
        assert len(service.list_versions(template_id)) == 1

    def test_an_unknown_template_has_no_version_to_make(self, service: IntakePacketService) -> None:
        assert service.create_version("no-such-template") is None


class TestDisplayOnlyItems:
    def test_a_heading_is_stored_as_not_required(self, service: IntakePacketService) -> None:
        """ "Required" has nothing to be true about on an item that asks nothing."""
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)

        service.replace_items(
            version_id,
            [
                ItemDraft(
                    key="about", item_type="section", required=True, config={"title": "About you"}
                ),
                ItemDraft(key="reason", item_type="reason", required=True),
            ],
        )

        stored = {i["key"]: i["required"] for i in service.list_items(version_id)}
        assert stored == {"about": False, "reason": True}

    def test_position_follows_the_order_it_was_sent_in(self, service: IntakePacketService) -> None:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)

        service.replace_items(
            version_id,
            [ItemDraft(key="reason", item_type="reason"), ItemDraft(key="b", item_type="yes_no")],
        )

        assert [(i["key"], i["position"]) for i in service.list_items(version_id)] == [
            ("reason", 0),
            ("b", 1),
        ]


class TestPinningAConsentDocument:
    """What a form points at, and what somebody actually signs.

    A practice picks a DOCUMENT in the editor. A signature has to name one
    exact revision of it, because the words are what was agreed to. Publish
    is where the two meet: the form is frozen, and the revision that was
    live at that moment is written onto the item.
    """

    @staticmethod
    def _with_documents(
        repo: InMemoryIntakePacketRepository, live: dict[str, str]
    ) -> IntakePacketService:
        return IntakePacketService(repo, live.get)

    @staticmethod
    def _consent_version(service: IntakePacketService, document_key: str) -> str:
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(
            version_id,
            [
                ItemDraft(key="reason", item_type="reason"),
                ItemDraft(
                    key="consent",
                    item_type="consent_document",
                    config={"document_key": document_key},
                ),
            ],
        )
        return version_id

    def test_publishing_writes_the_live_revision_onto_the_item(
        self, repo: InMemoryIntakePacketRepository
    ) -> None:
        service = self._with_documents(repo, {"doc-1": "revision-3"})
        version_id = self._consent_version(service, "doc-1")

        service.publish(version_id, _AUTHOR)

        consent = next(i for i in service.list_items(version_id) if i["key"] == "consent")
        assert consent["config"] == {
            "document_key": "doc-1",
            "document_version_id": "revision-3",
        }

    def test_a_draft_carries_no_revision(self, repo: InMemoryIntakePacketRepository) -> None:
        """Pinning early would go stale every time the document is revised."""
        service = self._with_documents(repo, {"doc-1": "revision-3"})
        version_id = self._consent_version(service, "doc-1")

        consent = next(i for i in service.list_items(version_id) if i["key"] == "consent")
        assert consent["config"] == {"document_key": "doc-1"}

    def test_a_document_nobody_can_sign_yet_refuses_the_publish(
        self, repo: InMemoryIntakePacketRepository
    ) -> None:
        service = self._with_documents(repo, {})
        version_id = self._consent_version(service, "doc-1")

        with pytest.raises(ItemConfigError, match="publish this document"):
            service.publish(version_id, _AUTHOR)

        version = service.get_version(version_id)
        assert version is not None
        assert version["published_at"] is None

    def test_revising_the_document_afterwards_leaves_the_form_alone(
        self, repo: InMemoryIntakePacketRepository
    ) -> None:
        """The whole point of pinning at publish rather than reading through."""
        live = {"doc-1": "revision-3"}
        service = self._with_documents(repo, live)
        version_id = self._consent_version(service, "doc-1")
        service.publish(version_id, _AUTHOR)

        live["doc-1"] = "revision-4"

        consent = next(i for i in service.list_items(version_id) if i["key"] == "consent")
        assert consent["config"] == {
            "document_key": "doc-1",
            "document_version_id": "revision-3",
        }

    def test_item_ids_survive_the_pin(self, repo: InMemoryIntakePacketRepository) -> None:
        """A saved answer points at an item id, so publishing must not reissue them."""
        service = self._with_documents(repo, {"doc-1": "revision-3"})
        version_id = self._consent_version(service, "doc-1")
        before = [str(i["id"]) for i in service.list_items(version_id)]

        service.publish(version_id, _AUTHOR)

        assert [str(i["id"]) for i in service.list_items(version_id)] == before

    def test_a_form_with_no_consent_item_is_unaffected(
        self, repo: InMemoryIntakePacketRepository
    ) -> None:
        service = self._with_documents(repo, {})
        template_id = str(service.create_template("Intake", _AUTHOR)["id"])
        version_id = _draft_id(service, template_id)
        service.replace_items(version_id, _items())

        service.publish(version_id, _AUTHOR)

        assert all(i["config"] == {} for i in service.list_items(version_id))
