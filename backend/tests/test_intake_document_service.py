# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The rules a stored consent document obeys.

Everything here is about one rule and its consequences: **a published
version never changes again.** The tests are grouped by what that rule
forces — that an edit to a published version is refused, that changing a
document means adding a version rather than rewriting one, and that the
digest a signature will record is maintained rather than computed at the
last moment.
"""

from __future__ import annotations

import pytest
from app.repositories import InMemoryIntakeDocumentRepository
from app.services.intake_document_service import (
    IntakeDocumentService,
    PublishedDocumentError,
    SignerRoleError,
)

_AUTHOR = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def service() -> IntakeDocumentService:
    return IntakeDocumentService(InMemoryIntakeDocumentRepository())


def _published(service: IntakeDocumentService, body: str = "Some text.") -> dict[str, object]:
    created = service.create(title="Consent", body_markdown=body)
    return service.publish(str(created["id"]), _AUTHOR)


class TestStartingADocument:
    def test_it_arrives_as_an_unpublished_version_one(self, service: IntakeDocumentService) -> None:
        created = service.create(title="Consent", body_markdown="Hello.")
        assert created["version"] == 1
        assert created["published_at"] is None
        assert created["published_by"] is None

    def test_the_patient_is_the_default_signer(self, service: IntakeDocumentService) -> None:
        created = service.create(title="Consent", body_markdown="Hello.")
        assert created["signer_roles"] == ["patient"]

    def test_signers_are_stored_in_one_order_however_they_arrive(
        self, service: IntakeDocumentService
    ) -> None:
        """Two documents asking for the same signatures read the same."""
        first = service.create(title="A", body_markdown="x", signer_roles=["guardian", "patient"])
        second = service.create(title="B", body_markdown="x", signer_roles=["patient", "guardian"])
        assert first["signer_roles"] == second["signer_roles"] == ["patient", "guardian"]

    def test_a_signer_nobody_recognises_is_refused(self, service: IntakeDocumentService) -> None:
        with pytest.raises(SignerRoleError, match="not somebody a document can ask to sign"):
            service.create(title="A", body_markdown="x", signer_roles=["lawyer"])

    def test_a_document_needs_somebody_to_sign_it(self, service: IntakeDocumentService) -> None:
        with pytest.raises(SignerRoleError, match="at least one person"):
            service.create(title="A", body_markdown="x", signer_roles=[])

    def test_each_document_gets_its_own_key(self, service: IntakeDocumentService) -> None:
        first = service.create(title="A", body_markdown="x")
        second = service.create(title="B", body_markdown="y")
        assert first["document_key"] != second["document_key"]


class TestTheDigest:
    def test_a_draft_carries_one_from_the_moment_it_exists(
        self, service: IntakeDocumentService
    ) -> None:
        created = service.create(title="Consent", body_markdown="Hello.")
        assert len(str(created["digest"])) == 64

    def test_editing_the_body_moves_it(self, service: IntakeDocumentService) -> None:
        created = service.create(title="Consent", body_markdown="Hello.")
        updated = service.update_draft(str(created["id"]), body_markdown="Goodbye.")
        assert updated["digest"] != created["digest"]

    def test_renaming_the_document_does_not(self, service: IntakeDocumentService) -> None:
        """The title is the practice's label. The words are what is signed."""
        created = service.create(title="Consent", body_markdown="Hello.")
        updated = service.update_draft(str(created["id"]), title="Consent to treatment")
        assert updated["digest"] == created["digest"]

    def test_publishing_does_not_move_it(self, service: IntakeDocumentService) -> None:
        """Freezing is not a write to the text, so it is not a write to this."""
        created = service.create(title="Consent", body_markdown="Hello.")
        published = service.publish(str(created["id"]), _AUTHOR)
        assert published["digest"] == created["digest"]


class TestTheFreeze:
    def test_a_published_version_cannot_be_edited(self, service: IntakeDocumentService) -> None:
        published = _published(service)
        with pytest.raises(PublishedDocumentError):
            service.update_draft(str(published["id"]), body_markdown="Rewritten.")

    def test_publishing_twice_is_refused(self, service: IntakeDocumentService) -> None:
        published = _published(service)
        with pytest.raises(PublishedDocumentError):
            service.publish(str(published["id"]), _AUTHOR)

    def test_publishing_records_who_and_when(self, service: IntakeDocumentService) -> None:
        published = _published(service)
        assert published["published_at"] is not None
        assert published["published_by"] == _AUTHOR

    def test_an_id_that_does_not_exist_is_a_lookup_error(
        self, service: IntakeDocumentService
    ) -> None:
        with pytest.raises(LookupError):
            service.publish("22222222-2222-2222-2222-222222222222", _AUTHOR)


class TestANewVersion:
    def test_it_carries_the_published_text_forward(self, service: IntakeDocumentService) -> None:
        published = _published(service, "Original words.")
        draft = service.new_version(str(published["id"]))
        assert draft["body_markdown"] == "Original words."
        assert draft["version"] == 2
        assert draft["published_at"] is None

    def test_it_keeps_the_document_key(self, service: IntakeDocumentService) -> None:
        """Which is what lets a form point at a document rather than a text."""
        published = _published(service)
        draft = service.new_version(str(published["id"]))
        assert draft["document_key"] == published["document_key"]

    def test_it_keeps_who_has_to_sign(self, service: IntakeDocumentService) -> None:
        created = service.create(
            title="Consent", body_markdown="x", signer_roles=["patient", "guardian"]
        )
        published = service.publish(str(created["id"]), _AUTHOR)
        draft = service.new_version(str(published["id"]))
        assert draft["signer_roles"] == ["patient", "guardian"]

    def test_asking_twice_hands_back_the_same_draft(self, service: IntakeDocumentService) -> None:
        """A practice can reach this from more than one screen."""
        published = _published(service)
        first = service.new_version(str(published["id"]))
        second = service.new_version(str(published["id"]))
        assert first["id"] == second["id"]

    def test_a_document_still_being_drafted_hands_back_that_draft(
        self, service: IntakeDocumentService
    ) -> None:
        created = service.create(title="Consent", body_markdown="x")
        assert service.new_version(str(created["id"]))["id"] == created["id"]

    def test_the_published_version_is_untouched(self, service: IntakeDocumentService) -> None:
        published = _published(service, "Original words.")
        draft = service.new_version(str(published["id"]))
        service.update_draft(str(draft["id"]), body_markdown="New words.")
        still = service.get(str(published["id"]))
        assert still is not None
        assert still["body_markdown"] == "Original words."
        assert still["digest"] == published["digest"]


class TestReadingThem:
    def test_the_list_shows_the_newest_version_of_each(
        self, service: IntakeDocumentService
    ) -> None:
        first = _published(service, "First document.")
        service.create(title="Second", body_markdown="Second document.")
        service.new_version(str(first["id"]))

        listed = service.list_documents()
        assert [row["version"] for row in listed] == [2, 1]
        assert [row["title"] for row in listed] == ["Consent", "Second"]

    def test_a_document_keeps_its_place_when_it_is_revised(
        self, service: IntakeDocumentService
    ) -> None:
        first = _published(service, "First.")
        service.create(title="Second", body_markdown="Second.")
        service.new_version(str(first["id"]))
        assert [row["title"] for row in service.list_documents()] == ["Consent", "Second"]

    def test_published_for_key_skips_the_draft(self, service: IntakeDocumentService) -> None:
        published = _published(service, "Live words.")
        draft = service.new_version(str(published["id"]))
        service.update_draft(str(draft["id"]), body_markdown="Not live yet.")

        live = service.published_for_key(str(published["document_key"]))
        assert live is not None
        assert live["id"] == published["id"]

    def test_published_for_key_is_none_before_anything_goes_live(
        self, service: IntakeDocumentService
    ) -> None:
        created = service.create(title="Consent", body_markdown="x")
        assert service.published_for_key(str(created["document_key"])) is None

    def test_the_form_editor_is_only_offered_published_documents(
        self, service: IntakeDocumentService
    ) -> None:
        _published(service, "Live.")
        service.create(title="Still being written", body_markdown="Draft.")
        assert [row["title"] for row in service.list_published()] == ["Consent"]

    def test_every_version_of_one_document_is_readable(
        self, service: IntakeDocumentService
    ) -> None:
        published = _published(service)
        service.new_version(str(published["id"]))
        versions = service.versions(str(published["document_key"]))
        assert [row["version"] for row in versions] == [2, 1]
