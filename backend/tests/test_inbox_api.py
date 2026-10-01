# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox: one list of what needs the clinician, read from where it lives.

What these tests hold the Inbox to, in order of how much they would hurt to
get wrong:

* **Each item leaves once it is handled where it lives** — a refill
  answered, a message replied to, a form accepted, a note signed, a calendar
  change settled — because the Inbox holds no copy of it.
* **Replying answers one message.** A client's earlier messages stay open
  unless the clinician says otherwise, once or as a standing choice, and
  every one that goes is in Done and can come back.
* Dismiss, snooze and restore are the Inbox's own, and are recorded.
* The count is the Open list's length, and a patient without a grant
  contributes nothing to either.

The Postgres half — the queries and the per-clinician row policy — is in
``tests_integration/database/test_inbox_db.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.inbox.registry import InboxContext, InboxRegistry, get_inbox_registry
from app.inbox.sources import (
    CalendarChangeSource,
    IntakeReviewSource,
    NoteToSignSource,
    PortalMessageSource,
    RefillSource,
    register_builtin_sources,
)
from app.main import app
from app.models import Patient, PatientMessage, PatientMessageThread, SessionStatus
from app.models.audit import AuditAction
from app.models.patient_message import SENDER_PATIENT
from app.models.refill_request import REFILL_STATUS_REQUESTED, RefillRequest
from app.models.session import TherapySession, Transcript
from app.portal.delivery import CapturingNoticeDelivery
from app.portal.factory import get_notice_delivery
from app.repositories import (
    InMemoryInboxItemStateRepository,
    InMemoryPatientIntakeAssignmentRepository,
    InMemoryPatientMessageRepository,
    InMemoryRefillRequestRepository,
)
from app.routes import inbox as inbox_routes
from app.routes import patient_messages as message_routes
from app.routes.patient_intake_assignments import get_clinician_patient_repository
from app.routes.refill_requests import (
    get_clinician_refill_request_repository,
    get_refill_request_repository,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.utcnow import utc_now

if TYPE_CHECKING:
    from app.models.inbox import InboxItem
    from app.repositories import (
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from app.services import AuditService
    from fastapi.testclient import TestClient

INBOX = "/api/inbox"
COUNT = "/api/inbox/count"
PREFERENCES = "/api/users/me/preferences"

_ADA = "11111111-1111-4111-8111-111111111111"
_GRACE = "22222222-2222-4222-8222-222222222222"
_STRANGER = "33333333-3333-4333-8333-333333333333"


def _id() -> str:
    return str(uuid.uuid4())


@dataclass
class World:
    messages: InMemoryPatientMessageRepository
    refills: InMemoryRefillRequestRepository
    intake: InMemoryPatientIntakeAssignmentRepository
    sessions: InMemoryTherapySessionRepository
    patients: InMemoryPatientRepository
    appointments: InMemoryAppointmentRepository
    states: InMemoryInboxItemStateRepository
    registry: InboxRegistry
    user_id: str


@pytest.fixture
def world(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> World:
    """Every built-in source over in-memory stores, wired into the app.

    The routes that handle an item where it lives (the refill decision, the
    reply) are pointed at the same stores, so "handled at its source" is the
    real route doing it.
    """
    now = utc_now()
    for patient_id, first, last in (
        (_ADA, "Ada", "Lovelace"),
        (_GRACE, "Grace", "Hopper"),
    ):
        mock_repo.create(
            Patient(
                id=patient_id,
                first_name=first,
                last_name=last,
                email=f"{first.lower()}@example.com",
                created_at=now,
                updated_at=now,
            ),
            mock_user_id,
        )
    world = World(
        messages=InMemoryPatientMessageRepository(),
        refills=InMemoryRefillRequestRepository(),
        intake=InMemoryPatientIntakeAssignmentRepository(),
        sessions=mock_session_repo,
        patients=mock_repo,
        appointments=InMemoryAppointmentRepository(),
        states=InMemoryInboxItemStateRepository(),
        registry=InboxRegistry(),
        user_id=mock_user_id,
    )
    world.messages.use_inbox_states(world.states)
    for patient_id, name in ((_ADA, "Ada Lovelace"), (_GRACE, "Grace Hopper")):
        world.messages.name_patient(patient_id, name)
        world.messages.grant_access(patient_id, mock_user_id)
        world.refills.grant_access(patient_id, mock_user_id)
        world.intake.grant_access(patient_id, mock_user_id)
        world.intake.name_patient(patient_id, name)
        world.appointments.grant_access(patient_id, mock_user_id)
    world.messages.name_patient(_STRANGER, "Someone Else")
    world.refills.add_patient(_ADA, "Ada", "Lovelace")

    for source in (
        PortalMessageSource(lambda: world.messages),
        RefillSource(lambda: world.refills),
        IntakeReviewSource(lambda: world.intake),
        NoteToSignSource(lambda: world.sessions, lambda: world.patients),
        CalendarChangeSource(lambda: world.appointments, lambda: world.patients),
    ):
        world.registry.register_source(source)

    overrides: dict[Any, Any] = {
        get_inbox_registry: lambda: world.registry,
        inbox_routes.get_inbox_state_repository: lambda: world.states,
        inbox_routes.get_inbox_message_repository: lambda: world.messages,
        message_routes.get_patient_message_repository: lambda: world.messages,
        message_routes.get_reply_inbox_state_repository: lambda: world.states,
        get_refill_request_repository: lambda: world.refills,
        get_clinician_refill_request_repository: lambda: world.refills,
        get_clinician_patient_repository: lambda: world.patients,
        get_notice_delivery: CapturingNoticeDelivery,
    }
    app.dependency_overrides.update(overrides)
    return world


# ── helpers ──────────────────────────────────────────────────────────────


def _client_message(
    world: World,
    thread_id: str,
    *,
    at: datetime,
    body: str,
    patient_id: str = _ADA,
    subject: str | None = "About Tuesday",
) -> str:
    """A message from the client, starting its thread if it is the first."""
    message = PatientMessage(
        id=_id(),
        thread_id=thread_id,
        patient_id=patient_id,
        sender=SENDER_PATIENT,
        body=body,
        created_at=at,
    )
    if world.messages.get_patient_thread(thread_id, patient_id) is None:
        thread = PatientMessageThread(
            id=thread_id,
            patient_id=patient_id,
            subject=subject,
            status="open",
            created_at=at,
            last_message_at=at,
        )
        world.messages.add_patient_thread(thread, message)
    else:
        world.messages.add_patient_message(message)
    return message.id


def _open(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get(INBOX, params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _done(client: TestClient) -> list[dict[str, Any]]:
    return _open(client, view="done")


def _ids(items: list[dict[str, Any]], kind: str | None = None) -> list[str]:
    return [item["source_id"] for item in items if kind is None or item["kind"] == kind]


def _reply(client: TestClient, thread_id: str, body: str, **extra: Any) -> dict[str, Any]:
    response = client.post(
        f"/api/message-threads/{thread_id}/replies", json={"body": body, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_preference(client: TestClient, value: str) -> None:
    prefs = client.get(PREFERENCES).json()
    saved = client.put(PREFERENCES, json={**prefs, "inbox_reply_earlier_messages": value})
    assert saved.status_code == 200, saved.text


def _entries(audit: AuditService) -> list[Any]:
    return [call.args[0] for call in audit._repo.append.call_args_list]


def _refill(world: World) -> str:
    now = utc_now()
    request = RefillRequest(
        id=_id(),
        patient_id=_ADA,
        medication_id=None,
        medication_text="Sertraline 50 mg",
        pharmacy_text=None,
        patient_note="Out on Friday",
        status=REFILL_STATUS_REQUESTED,
        created_at=now,
        updated_at=now,
    )
    world.refills.add(request)
    return request.id


# ── each source: listed while open, gone once handled where it lives ─────


class TestHandledAtItsSource:
    def test_a_refill_leaves_once_it_is_decided(self, world: World, client: TestClient) -> None:
        refill_id = _refill(world)

        [item] = _open(client)
        assert item["kind"] == "refill"
        assert item["source_id"] == refill_id
        assert item["title"] == "Refill request: Sertraline 50 mg"
        assert item["detail"] == "Out on Friday"
        assert item["patient_name"] == "Ada Lovelace"
        assert item["href"] == "/dashboard/refills"
        assert item["severity"] == "normal"

        decided = client.post(
            f"/api/refill-requests/{refill_id}/decision", json={"status": "approved"}
        )
        assert decided.status_code == 200, decided.text

        assert _open(client) == []

    def test_a_message_leaves_once_it_is_replied_to(self, world: World, client: TestClient) -> None:
        thread_id = _id()
        message_id = _client_message(world, thread_id, at=utc_now(), body="Can we move to 3?")

        [item] = _open(client)
        assert item["kind"] == "portal_message"
        assert item["source_id"] == message_id
        assert item["title"] == "About Tuesday"
        assert item["detail"] == "Can we move to 3?"
        assert item["context"] == {"thread_id": thread_id}

        reply = _reply(client, thread_id, "3 works.", in_reply_to_message_id=message_id)
        assert reply["inbox"]["resolved_ids"] == [message_id]

        assert _open(client) == []
        [done] = _done(client)
        assert done["source_id"] == message_id
        assert done["disposition"] == "replied"

    def test_a_form_leaves_once_it_is_accepted(self, world: World, client: TestClient) -> None:
        now = utc_now()
        assignment_id, version_id = _id(), _id()
        world.intake.name_form(version_id, "New client intake")
        world.intake.add_assignment(
            {
                "id": assignment_id,
                "patient_id": _GRACE,
                "version_id": version_id,
                "status": "submitted",
                "assigned_at": now,
                "submitted_at": now,
                "updated_at": now,
            },
            world.user_id,
        )

        [item] = _open(client)
        assert item["kind"] == "intake_review"
        assert item["title"] == "Form to review: New client intake"
        assert item["patient_name"] == "Grace Hopper"
        assert item["href"] == f"/dashboard/patients/{_GRACE}"

        world.intake.set_status_for_clinician(
            assignment_id, world.user_id, status="accepted", now=utc_now()
        )

        assert _open(client) == []

    def test_a_note_leaves_once_it_is_signed(self, world: World, client: TestClient) -> None:
        session = TherapySession(
            id=_id(),
            user_id=world.user_id,
            patient_id=_ADA,
            session_date=datetime(2026, 9, 29, 15, 0, tzinfo=UTC),
            session_number=1,
            status=SessionStatus.PENDING_REVIEW,
            transcript=Transcript(format="txt", content="x"),
            created_at=utc_now(),
        )
        world.sessions.create(session)

        [item] = _open(client)
        assert item["kind"] == "note_to_sign"
        assert item["title"] == "Note to review and sign"
        assert item["href"] == f"/dashboard/sessions/{session.id}"
        assert item["patient_name"] == "Ada Lovelace"

        session.status = SessionStatus.FINALIZED
        world.sessions.update(session)

        assert _open(client) == []

    def test_a_calendar_change_leaves_once_it_is_settled(
        self, world: World, client: TestClient
    ) -> None:
        now = utc_now()

        def appointment(start: datetime, status: str) -> Appointment:
            return world.appointments.create(
                Appointment(
                    id=_id(),
                    user_id=world.user_id,
                    patient_id=_ADA,
                    title="Session",
                    start_at=start,
                    end_at=start + timedelta(minutes=50),
                    duration_minutes=50,
                    status=AppointmentStatus.CANCELLED,
                    session_type="individual",
                    google_sync_status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

        upcoming = appointment(now + timedelta(days=2), "removed_in_google")
        appointment(now - timedelta(days=2), "removed_in_google")  # past: nothing to undo
        appointment(now + timedelta(days=3), "synced")

        [item] = _open(client)
        assert item["kind"] == "calendar_change"
        assert item["source_id"] == upcoming.id
        assert item["title"] == "Removed from Google Calendar, so cancelled here."
        assert item["context"]["google_sync_status"] == "removed_in_google"
        assert item["context"]["appointment_id"] == upcoming.id

        upcoming.google_sync_status = None  # the clinician accepted the cancellation
        world.appointments.update(upcoming)

        assert _open(client) == []


# ── one client, three messages ───────────────────────────────────────────


@pytest.fixture
def three_messages(world: World) -> tuple[str, list[str]]:
    """Three messages from Ada in one conversation, oldest first."""
    thread_id = _id()
    start = utc_now() - timedelta(hours=3)
    ids = [
        _client_message(world, thread_id, at=start + timedelta(hours=n), body=f"message {n}")
        for n in range(3)
    ]
    return thread_id, ids


class TestReplyingToOneOfThree:
    def test_replying_to_one_leaves_the_other_two_open_and_asks(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages

        reply = _reply(client, thread_id, "Got it.", in_reply_to_message_id=third)

        assert reply["inbox"] == {
            "resolved_ids": [third],
            "earlier_open_ids": [second, first],
            "earlier_handled_ids": [],
        }
        assert set(_ids(_open(client))) == {first, second}

    def test_yes_marks_the_earlier_ones_handled_this_time_only(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages
        _reply(client, thread_id, "Got it.", in_reply_to_message_id=third)

        response = client.post(f"{INBOX}/portal_message/{third}/handle-earlier")

        assert response.status_code == 200, response.text
        assert set(response.json()["handled_ids"]) == {first, second}
        assert _open(client) == []
        done = {item["source_id"]: item["disposition"] for item in _done(client)}
        assert done == {third: "replied", second: "handled", first: "handled"}
        # "Yes" is this time only: the standing choice is still to ask.
        assert client.get(PREFERENCES).json()["inbox_reply_earlier_messages"] == "ask"

    def test_always_is_remembered_and_marks_them_handled_with_the_reply(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages
        _set_preference(client, "always")
        assert client.get(PREFERENCES).json()["inbox_reply_earlier_messages"] == "always"

        reply = _reply(client, thread_id, "Got it.", in_reply_to_message_id=third)

        assert reply["inbox"]["earlier_open_ids"] == []
        assert reply["inbox"]["earlier_handled_ids"] == [second, first]
        assert _open(client) == []

        # Undo is a restore of each: nothing was deleted.
        for message_id in reply["inbox"]["earlier_handled_ids"]:
            restored = client.post(f"{INBOX}/portal_message/{message_id}/restore")
            assert restored.status_code == 200, restored.text
        assert set(_ids(_open(client))) == {first, second}

    def test_dont_ask_again_is_remembered_and_leaves_them_open(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages
        _set_preference(client, "never")

        reply = _reply(client, thread_id, "Got it.", in_reply_to_message_id=third)

        assert reply["inbox"] == {
            "resolved_ids": [third],
            "earlier_open_ids": [],
            "earlier_handled_ids": [],
        }
        assert set(_ids(_open(client))) == {first, second}

    def test_changing_the_setting_changes_what_the_next_reply_does(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages
        _set_preference(client, "never")
        _reply(client, thread_id, "One.", in_reply_to_message_id=third)
        _set_preference(client, "always")

        reply = _reply(client, thread_id, "Two.", in_reply_to_message_id=second)

        assert reply["inbox"]["earlier_handled_ids"] == [first]
        assert _open(client) == []

    def test_a_reply_that_names_no_message_answers_the_newest_waiting(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (first, second, third) = three_messages

        reply = _reply(client, thread_id, "Got it.")

        assert reply["inbox"]["resolved_ids"] == [third]
        assert set(_ids(_open(client))) == {first, second}

    def test_a_message_from_another_conversation_is_refused_before_sending(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, _ = three_messages
        elsewhere = _client_message(world, _id(), at=utc_now(), body="other", patient_id=_GRACE)

        response = client.post(
            f"/api/message-threads/{thread_id}/replies",
            json={"body": "Got it.", "in_reply_to_message_id": elsewhere},
        )

        assert response.status_code == 422
        sent = [
            m
            for m in world.messages.list_messages(thread_id, world.user_id)
            if m.sender != SENDER_PATIENT
        ]
        assert sent == []

    def test_earlier_messages_are_this_clients_only(
        self, world: World, client: TestClient, three_messages: tuple[str, list[str]]
    ) -> None:
        thread_id, (_, _, third) = three_messages
        other = _client_message(
            world, _id(), at=utc_now() - timedelta(days=1), body="hi", patient_id=_GRACE
        )

        reply = _reply(client, thread_id, "Got it.", in_reply_to_message_id=third)

        assert other not in reply["inbox"]["earlier_open_ids"]
        assert other in _ids(_open(client))


# ── the Inbox's own actions ──────────────────────────────────────────────


class TestDismissSnoozeRestore:
    def test_dismiss_moves_an_item_to_done_and_restore_brings_it_back(
        self, world: World, client: TestClient, mock_audit_service: AuditService
    ) -> None:
        refill_id = _refill(world)

        dismissed = client.post(f"{INBOX}/refill/{refill_id}/dismiss")

        assert dismissed.status_code == 200, dismissed.text
        assert dismissed.json()["disposition"] == "dismissed"
        assert _open(client) == []
        [done] = _done(client)
        assert (done["source_id"], done["disposition"]) == (refill_id, "dismissed")

        restored = client.post(f"{INBOX}/refill/{refill_id}/restore")

        assert restored.status_code == 200
        assert _ids(_open(client)) == [refill_id]
        assert _done(client) == []
        actions = [(e.action, e.resource_id, e.patient_id) for e in _entries(mock_audit_service)]
        assert (AuditAction.INBOX_ITEM_DISMISSED.value, f"refill:{refill_id}", _ADA) in actions
        assert (AuditAction.INBOX_ITEM_RESTORED.value, f"refill:{refill_id}", _ADA) in actions

    def test_snooze_hides_an_item_until_its_time(self, world: World, client: TestClient) -> None:
        refill_id = _refill(world)
        until = utc_now() + timedelta(days=1)

        snoozed = client.post(
            f"{INBOX}/refill/{refill_id}/snooze", json={"until": until.isoformat()}
        )

        assert snoozed.status_code == 200, snoozed.text
        assert _open(client) == []
        [done] = _done(client)
        assert done["disposition"] == "snoozed"
        assert datetime.fromisoformat(done["snoozed_until"]) == until

    def test_a_snooze_that_has_run_out_is_open_again(
        self, world: World, client: TestClient
    ) -> None:
        refill_id = _refill(world)
        world.states.record(
            world.user_id,
            ("refill", refill_id),
            "snoozed",
            utc_now() - timedelta(days=2),
            snoozed_until=utc_now() - timedelta(minutes=1),
        )

        assert _ids(_open(client)) == [refill_id]
        assert _done(client) == []

    @pytest.mark.parametrize("delta", [timedelta(minutes=-1), timedelta(days=91)])
    def test_a_snooze_must_be_in_the_near_future(
        self, world: World, client: TestClient, delta: timedelta
    ) -> None:
        refill_id = _refill(world)
        until = utc_now() + delta

        response = client.post(
            f"{INBOX}/refill/{refill_id}/snooze", json={"until": until.isoformat()}
        )

        assert response.status_code == 422
        assert _ids(_open(client)) == [refill_id]

    @pytest.mark.parametrize(
        "path",
        [
            "nonsense/11111111-1111-4111-8111-111111111111/dismiss",
            "refill/44444444-4444-4444-8444-444444444444/dismiss",
            "refill/not-a-uuid/dismiss",
            "portal_message/not-a-uuid/handle-earlier",
        ],
    )
    def test_an_item_nobody_can_see_is_not_found(
        self, world: World, client: TestClient, path: str
    ) -> None:
        assert client.post(f"{INBOX}/{path}").status_code == 404

    def test_restoring_does_not_bring_back_what_its_source_finished(
        self, world: World, client: TestClient
    ) -> None:
        refill_id = _refill(world)
        client.post(f"{INBOX}/refill/{refill_id}/dismiss")
        client.post(f"/api/refill-requests/{refill_id}/decision", json={"status": "declined"})

        client.post(f"{INBOX}/refill/{refill_id}/restore")

        assert _open(client) == []


# ── the list and the count ───────────────────────────────────────────────


class TestListAndCount:
    def test_the_count_is_the_open_list(
        self, world: World, client: TestClient, mock_audit_service: AuditService
    ) -> None:
        refill_id = _refill(world)
        _client_message(world, _id(), at=utc_now(), body="hello")

        assert client.get(COUNT).json() == {"count": 2}

        client.post(f"{INBOX}/refill/{refill_id}/dismiss")

        assert client.get(COUNT).json() == {"count": 1}
        counted = [
            e for e in _entries(mock_audit_service) if e.action == AuditAction.INBOX_COUNTED.value
        ]
        assert counted[-1].changes == {"open_items": 1}
        assert counted[-1].patient_id is None

    def test_kinds_narrows_the_list(self, world: World, client: TestClient) -> None:
        refill_id = _refill(world)
        message_id = _client_message(world, _id(), at=utc_now(), body="hello")

        assert _ids(_open(client, kinds="refill")) == [refill_id]
        assert _ids(_open(client, kinds="portal_message")) == [message_id]
        assert set(_ids(_open(client))) == {refill_id, message_id}

    def test_a_patient_without_a_grant_contributes_nothing(
        self, world: World, client: TestClient, mock_audit_service: AuditService
    ) -> None:
        _client_message(world, _id(), at=utc_now(), body="not yours", patient_id=_STRANGER)

        assert _open(client) == []
        assert client.get(COUNT).json() == {"count": 0}
        assert all(e.patient_id != _STRANGER for e in _entries(mock_audit_service))

    def test_the_list_is_recorded_once_per_patient_on_it(
        self, world: World, client: TestClient, mock_audit_service: AuditService
    ) -> None:
        _refill(world)
        _client_message(world, _id(), at=utc_now(), body="one")
        _client_message(world, _id(), at=utc_now(), body="two", patient_id=_GRACE)

        _open(client)

        viewed = [
            e for e in _entries(mock_audit_service) if e.action == AuditAction.INBOX_VIEWED.value
        ]
        by_patient = {e.patient_id: e.changes for e in viewed}
        assert by_patient == {
            _ADA: {"view": "open", "item_count": 2, "kinds": ["portal_message", "refill"]},
            _GRACE: {"view": "open", "item_count": 1, "kinds": ["portal_message"]},
        }
        assert "one" not in str([e.changes for e in viewed])

    def test_urgent_first_then_newest(self, world: World, client: TestClient) -> None:
        old = _client_message(world, _id(), at=utc_now() - timedelta(days=2), body="old")
        new = _client_message(world, _id(), at=utc_now(), body="new")
        urgent = _client_message(world, _id(), at=utc_now() - timedelta(days=5), body="help")

        class Flag:
            def enrich(self, items: list[InboxItem], ctx: InboxContext) -> list[InboxItem]:
                return [
                    item.model_copy(update={"severity": "urgent"})
                    if item.source_id == urgent
                    else item
                    for item in items
                ]

        world.registry.register_enricher(Flag())

        assert _ids(_open(client)) == [urgent, new, old]


# ── the seams ────────────────────────────────────────────────────────────


class TestRegistry:
    def test_builtins_do_not_replace_a_deployments_own_source(self) -> None:
        registry = InboxRegistry()

        class Mine:
            kind = "refill"

            def list_open(self, ctx: InboxContext) -> list[InboxItem]:
                return []

            def get_items(self, ctx: InboxContext, source_ids: Any) -> list[InboxItem]:
                return []

        mine = Mine()
        registry.register_source(mine)

        register_builtin_sources(registry)

        assert registry.source("refill") is mine
        assert {s.kind for s in registry.sources} == {
            "portal_message",
            "refill",
            "intake_review",
            "note_to_sign",
            "calendar_change",
        }
