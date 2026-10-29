# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Sign and lock a note, add to it after, and unlock it to correct an error.

Service-level tests drive :class:`NoteService` over the in-memory repository;
the HTTP tests drive ``/api/notes/{id}/sign``, ``/unlock``, ``/addenda`` and
``/signing`` through the app with the shared conftest overrides. The addendum
chain tests mirror the prescribing addendum tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models import Note
from app.models.audit import AuditAction
from app.repositories import InMemoryNotesRepository
from app.routes.notes import get_notes_repository as get_notes_route_notes_repository
from app.services.note_service import NoteService
from app.services.note_signing import (
    AddendumTextRequiredError,
    NoteAlreadySignedError,
    NoteLockedError,
    NoteNotLockedError,
    NoteNotSignedError,
    SignatureNameRequiredError,
    UnlockReasonRequiredError,
    verify_addenda_chain,
    verify_signature,
)

if TYPE_CHECKING:
    from app.models import User
    from app.services import AuditService
    from fastapi.testclient import TestClient

_USER = "clinician-1"
_SOAP: dict[str, Any] = {
    "subjective": "S",
    "objective": "O",
    "assessment": "A",
    "plan": "P",
}


def _note(
    repo: InMemoryNotesRepository, *, finalized: bool = False, owner: str | None = None
) -> Note:
    now = datetime.now(UTC)
    note = Note(
        id=str(uuid.uuid4()),
        patient_id="patient-1",
        session_id=None,
        note_type="soap",
        content=_SOAP,
        created_at=now,
        updated_at=now,
        finalized_at=now - timedelta(days=30) if finalized else None,
    )
    return repo.add(note, owner) if owner else repo.add(note)


@pytest.fixture
def repo() -> InMemoryNotesRepository:
    r = InMemoryNotesRepository()
    r.grant_all_access()
    return r


@pytest.fixture
def service(repo: InMemoryNotesRepository) -> NoteService:
    return NoteService(repo)


def _sign(service: NoteService, note_id: str, name: str = "Sam Ortiz", creds: str = "LMFT") -> Any:
    return service.sign_note(note_id, signer_name=name, signer_credentials=creds, user_id=_USER)


# --------------------------------------------------------------------------
# Sign and lock
# --------------------------------------------------------------------------


class TestSign:
    def test_persists_the_signature_exactly_as_entered(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        signed, signature = service.sign_note(
            note.id,
            signer_name="  Sam Ortiz ",
            signer_credentials=" PhD, LMFT ",
            user_id=_USER,
        )
        assert signature.signer_name == "Sam Ortiz"
        assert signature.signer_credentials == "PhD, LMFT"
        assert signature.signed_by == _USER
        assert signature.version == 1
        assert signed.finalized_at == signature.signed_at
        assert signature.content == _SOAP
        assert verify_signature(signature)

    def test_blank_credentials_are_stored_as_none(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _, signature = _sign(service, note.id, creds="   ")
        assert signature.signer_credentials is None

    def test_a_blank_name_is_refused(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        with pytest.raises(SignatureNameRequiredError):
            _sign(service, note.id, name="  ")
        assert repo.get(note.id) is not None
        assert repo.get(note.id).finalized_at is None  # type: ignore[union-attr]

    def test_signing_twice_is_refused(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _sign(service, note.id)
        with pytest.raises(NoteAlreadySignedError):
            _sign(service, note.id)

    def test_editing_a_signed_note_is_refused(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _sign(service, note.id)
        with pytest.raises(NoteLockedError):
            service.update_note_edits(note.id, {**_SOAP, "plan": "changed"}, _USER)

    def test_a_signed_version_detects_tampering(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _, signature = _sign(service, note.id)
        signature.signer_credentials = "MD"
        assert not verify_signature(signature)

    def test_a_legacy_finalized_note_can_be_signed_and_keeps_its_date(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo, finalized=True)
        finalized_at = note.finalized_at
        signed, signature = _sign(service, note.id)
        assert signed.finalized_at == finalized_at
        assert signature.version == 1


# --------------------------------------------------------------------------
# Addenda
# --------------------------------------------------------------------------


class TestAddenda:
    def test_are_attributed_timestamped_signed_and_chained(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _sign(service, note.id)
        _, first = service.add_addendum(
            note.id,
            text="Client called after session.",
            signer_name="Sam Ortiz",
            signer_credentials="LMFT",
            user_id=_USER,
        )
        _, second = service.add_addendum(
            note.id,
            text="Safety plan reviewed by phone.",
            signer_name="Sam Ortiz",
            signer_credentials=None,
            user_id=_USER,
        )
        assert first.created_by == _USER
        assert first.created_at is not None
        assert first.signer_credentials == "LMFT"
        assert first.prev_digest is None
        assert second.prev_digest == first.digest
        _, _, addenda = service.get_signing_record(note.id, _USER)
        assert [a.id for a in addenda] == [first.id, second.id]
        assert verify_addenda_chain(addenda) is True

    def test_never_change_the_note_body(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _sign(service, note.id)
        before = (dict(note.content or {}), note.content_edited)
        service.add_addendum(
            note.id, text="Added later.", signer_name="Sam", signer_credentials=None, user_id=_USER
        )
        after = repo.get(note.id)
        assert after is not None
        assert (after.content, after.content_edited) == before

    def test_need_a_locked_note(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        note = _note(repo)
        with pytest.raises(NoteNotLockedError):
            service.add_addendum(
                note.id, text="x", signer_name="Sam", signer_credentials=None, user_id=_USER
            )

    def test_need_text(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        note = _note(repo)
        _sign(service, note.id)
        with pytest.raises(AddendumTextRequiredError):
            service.add_addendum(
                note.id, text="  ", signer_name="Sam", signer_credentials=None, user_id=_USER
            )

    def test_a_legacy_finalized_note_takes_addenda(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo, finalized=True)
        _, addendum = service.add_addendum(
            note.id, text="Late entry.", signer_name="Sam", signer_credentials=None, user_id=_USER
        )
        assert addendum.prev_digest is None


class TestAddendaChain:
    """Mirrors the prescribing addendum chain tests."""

    def _two(self, service: NoteService, repo: InMemoryNotesRepository) -> list[Any]:
        note = _note(repo)
        _sign(service, note.id)
        for text in ("first", "second"):
            service.add_addendum(
                note.id, text=text, signer_name="Sam", signer_credentials=None, user_id=_USER
            )
        return service.get_signing_record(note.id, _USER)[2]

    def test_intact(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        assert verify_addenda_chain(self._two(service, repo)) is True
        assert verify_addenda_chain([]) is True

    def test_detects_edited_text(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        addenda = self._two(service, repo)
        addenda[0].text = "rewritten"
        assert verify_addenda_chain(addenda) is False

    def test_detects_reorder(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        first, second = self._two(service, repo)
        assert verify_addenda_chain([second, first]) is False

    def test_detects_removal(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        _, second = self._two(service, repo)
        assert verify_addenda_chain([second]) is False


# --------------------------------------------------------------------------
# Unlock
# --------------------------------------------------------------------------


class TestUnlock:
    def test_requires_a_reason(self, service: NoteService, repo: InMemoryNotesRepository) -> None:
        note = _note(repo)
        _sign(service, note.id)
        with pytest.raises(UnlockReasonRequiredError):
            service.unlock_note(note.id, reason="   ", user_id=_USER)
        assert repo.get(note.id).finalized_at is not None  # type: ignore[union-attr]

    def test_keeps_the_signed_version_and_makes_the_note_editable(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _, first = _sign(service, note.id)
        unlocked, superseded = service.unlock_note(
            note.id, reason="Wrong date of service", user_id=_USER
        )
        assert unlocked.finalized_at is None
        assert superseded.id == first.id
        assert superseded.unlock_reason == "Wrong date of service"
        assert superseded.unlocked_by == _USER
        assert superseded.unlocked_at is not None

        edited = service.update_note_edits(note.id, {**_SOAP, "plan": "corrected"}, _USER)
        assert edited.content_edited == {**_SOAP, "plan": "corrected"}

        _, second = _sign(service, note.id, creds="LMFT, LPCC")
        assert second.version == 2
        assert second.content_edited == {**_SOAP, "plan": "corrected"}

        _, versions, _ = service.get_signing_record(note.id, _USER)
        assert [v.version for v in versions] == [1, 2]
        kept = versions[0]
        assert kept.content == _SOAP
        assert kept.content_edited is None
        assert kept.signer_credentials == "LMFT"
        assert kept.unlock_reason == "Wrong date of service"
        assert verify_signature(kept)
        assert versions[1].unlocked_at is None

    def test_addenda_stay_with_the_note_across_an_unlock(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        _sign(service, note.id)
        service.add_addendum(
            note.id, text="Added.", signer_name="Sam", signer_credentials=None, user_id=_USER
        )
        service.unlock_note(note.id, reason="Typo in plan", user_id=_USER)
        _sign(service, note.id)
        _, _, addenda = service.get_signing_record(note.id, _USER)
        assert [a.text for a in addenda] == ["Added."]

    def test_an_unlocked_note_cannot_be_unlocked(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo)
        with pytest.raises(NoteNotLockedError):
            service.unlock_note(note.id, reason="x", user_id=_USER)

    def test_a_legacy_note_has_no_version_to_supersede(
        self, service: NoteService, repo: InMemoryNotesRepository
    ) -> None:
        note = _note(repo, finalized=True)
        with pytest.raises(NoteNotSignedError):
            service.unlock_note(note.id, reason="x", user_id=_USER)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _actions(audit: AuditService) -> list[Any]:
    return [call.args[0] for call in audit._repo.append.call_args_list]  # type: ignore[attr-defined]


class TestRoutes:
    def test_sign_then_a_profile_change_leaves_the_block_alone(
        self,
        client: TestClient,
        mock_notes_repo: InMemoryNotesRepository,
        mock_user: User,
        mock_user_id: str,
    ) -> None:
        note = _note(mock_notes_repo)
        response = client.post(
            f"/api/notes/{note.id}/sign",
            json={"signer_name": "Sam Ortiz", "signer_credentials": "LMFT (edited)"},
        )
        assert response.status_code == 200
        assert response.json()["finalized_at"] is not None

        mock_user.name = "Samuel Ortiz-Reyes"
        mock_user.credentials = "PhD"

        record = client.get(f"/api/notes/{note.id}/signing").json()
        assert record["signature"]["signer_name"] == "Sam Ortiz"
        assert record["signature"]["signer_credentials"] == "LMFT (edited)"
        assert record["signature"]["signed_by"] == mock_user_id
        assert record["versions"][0]["id"] == record["signature"]["id"]
        assert record["addenda"] == []

    def test_editing_a_signed_note_is_409(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo)
        client.post(f"/api/notes/{note.id}/sign", json={"signer_name": "Sam"})
        response = client.patch(
            f"/api/notes/{note.id}", json={"content_edited": {**_SOAP, "plan": "x"}}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "NOTE_LOCKED"

    def test_editing_a_legacy_finalized_note_is_409(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo, finalized=True)
        response = client.patch(f"/api/notes/{note.id}", json={"content_edited": _SOAP})
        assert response.status_code == 409

    def test_unlock_without_a_reason_is_400(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo)
        client.post(f"/api/notes/{note.id}/sign", json={"signer_name": "Sam"})
        response = client.post(f"/api/notes/{note.id}/unlock", json={"reason": " "})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "UNLOCK_REASON_REQUIRED"

    def test_the_whole_cycle_is_audited_without_the_reason_text(
        self,
        client: TestClient,
        mock_notes_repo: InMemoryNotesRepository,
        mock_audit_service: AuditService,
    ) -> None:
        note = _note(mock_notes_repo)
        assert (
            client.post(f"/api/notes/{note.id}/sign", json={"signer_name": "Sam"}).status_code
            == 200
        )
        added = client.post(
            f"/api/notes/{note.id}/addenda",
            json={"text": "Called after.", "signer_name": "Sam", "signer_credentials": "LMFT"},
        )
        assert added.status_code == 201
        assert added.json()["signer_credentials"] == "LMFT"
        unlocked = client.post(
            f"/api/notes/{note.id}/unlock", json={"reason": "Wrong client initials"}
        )
        assert unlocked.status_code == 200
        assert unlocked.json()["finalized_at"] is None
        assert (
            client.patch(f"/api/notes/{note.id}", json={"content_edited": _SOAP}).status_code == 200
        )
        assert (
            client.post(f"/api/notes/{note.id}/sign", json={"signer_name": "Sam"}).status_code
            == 200
        )

        record = client.get(f"/api/notes/{note.id}/signing").json()
        assert [v["version"] for v in record["versions"]] == [1, 2]
        assert record["versions"][0]["unlock_reason"] == "Wrong client initials"
        assert record["signature"]["version"] == 2
        assert [a["text"] for a in record["addenda"]] == ["Called after."]

        entries = [e for e in _actions(mock_audit_service) if e.resource_id == note.id]
        actions = [e.action for e in entries]
        assert actions.count(AuditAction.NOTE_SIGNED.value) == 2
        assert AuditAction.NOTE_ADDENDUM_ADDED.value in actions
        assert AuditAction.NOTE_UNLOCKED.value in actions
        for entry in entries:
            assert "Wrong client initials" not in repr(entry.changes)
            assert "Called after." not in repr(entry.changes)

    def test_an_addendum_on_an_unlocked_note_is_409(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo)
        response = client.post(
            f"/api/notes/{note.id}/addenda", json={"text": "x", "signer_name": "Sam"}
        )
        assert response.status_code == 409

    def test_an_unsigned_note_has_no_block(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo)
        record = client.get(f"/api/notes/{note.id}/signing").json()
        assert record["signature"] is None
        assert record["finalized_at"] is None
        assert record["versions"] == []

    def test_a_legacy_note_reports_finalized_with_no_signature(
        self, client: TestClient, mock_notes_repo: InMemoryNotesRepository
    ) -> None:
        note = _note(mock_notes_repo, finalized=True)
        record = client.get(f"/api/notes/{note.id}/signing").json()
        assert record["signature"] is None
        assert record["finalized_at"] is not None

    @pytest.mark.parametrize(
        ("method", "suffix", "body"),
        [
            ("get", "/signing", None),
            ("post", "/sign", {"signer_name": "Sam"}),
            ("post", "/unlock", {"reason": "x"}),
            ("post", "/addenda", {"text": "x", "signer_name": "Sam"}),
        ],
    )
    def test_another_clinicians_note_is_404(
        self,
        client: TestClient,
        method: str,
        suffix: str,
        body: dict[str, str] | None,
    ) -> None:
        foreign = InMemoryNotesRepository()
        foreign.grant_access("patient-1", "someone-else")
        note = _note(foreign, owner="someone-else")
        app.dependency_overrides[get_notes_route_notes_repository] = lambda: foreign
        try:
            kwargs = {"json": body} if body is not None else {}
            response = getattr(client, method)(f"/api/notes/{note.id}{suffix}", **kwargs)
        finally:
            app.dependency_overrides.pop(get_notes_route_notes_repository, None)
        assert response.status_code == 404
