# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the intake form builder.

Three things are being checked here that the service tests cannot see: the
door, the status codes, and the audit row.

**The door.** These routes are the ordinary clinician surface, so an
unauthenticated caller is a 401 and a patient credential is a 401 — the
identity verifier refuses it before any handler runs. Tests that need a
signed-in clinician use the shared ``client`` fixture, which overrides that
door; the two that test the door do not, and drive the real app.

**The status codes.** A 409 means the version is published and the change
belongs on a new one. A 422 means the form does not make sense yet, and the
body names the item that does not — that string is what the editor puts
beside the question, so it is asserted rather than assumed.

**The audit row.** Publishing is the only write here that is logged, and
what it logs is which version went live and how many questions it asks.
Never a question.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models.audit import AuditAction
from app.repositories import InMemoryIntakePacketRepository
from app.routes.intake_packets import get_intake_packet_service
from app.services.intake_packet_service import IntakePacketService
from fastapi import HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.services import AuditService

BASE = "/api/intake/templates"


@pytest.fixture
def packet_repo() -> InMemoryIntakePacketRepository:
    return InMemoryIntakePacketRepository()


@pytest.fixture
def intake_client(client: TestClient, packet_repo: InMemoryIntakePacketRepository) -> TestClient:
    """The shared clinician client, with the packet store in memory."""
    app.dependency_overrides[get_intake_packet_service] = lambda: IntakePacketService(packet_repo)
    return client


def _entries(audit: AuditService) -> list[Any]:
    return [call.args[0] for call in audit._repo.append.call_args_list]


def _create(client: TestClient, name: str = "Intake") -> dict[str, Any]:
    response = client.post(BASE, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _draft_id(template: dict[str, Any]) -> str:
    return str(template["versions"][0]["id"])


def _items(client: TestClient, template_id: str, version_id: str, items: list[dict]) -> Any:
    return client.put(f"{BASE}/{template_id}/versions/{version_id}/items", json={"items": items})


_DEFAULT_ITEMS = [
    {"key": "demographics", "item_type": "demographics"},
    {"key": "reason", "item_type": "reason"},
]


class TestTheDoor:
    def test_without_a_credential_is_401(self) -> None:
        """No overrides here: the real clinician door answers."""
        assert TestClient(app).get(BASE).status_code == 401

    def test_a_patient_bearer_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A patient credential is a bearer token and not a clinician identity.

        There is no patient-principal fallback on this surface to pick the
        credential up after the clinician verifier refuses it, which is what
        the assertion is worth — a patient has no business in the practice's
        own form builder.
        """

        def _not_a_clinician_identity(_token: str) -> dict[str, Any]:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "INVALID_TOKEN", "message": "", "details": {}}},
            )

        monkeypatch.setattr("app.auth.service.verify_token", _not_a_clinician_identity)

        response = TestClient(app).get(
            BASE, headers={"Authorization": "Bearer credential-of-a-patient"}
        )
        assert response.status_code == 401


class TestTemplates:
    def test_a_new_form_arrives_with_a_draft(self, intake_client: TestClient) -> None:
        body = _create(intake_client)

        assert body["name"] == "Intake"
        assert len(body["versions"]) == 1
        assert body["versions"][0]["published_at"] is None

    def test_listing_shows_what_the_practice_has_built(self, intake_client: TestClient) -> None:
        _create(intake_client, "New patients")
        _create(intake_client, "Annual review")

        names = [t["name"] for t in intake_client.get(BASE).json()]
        assert names == ["New patients", "Annual review"]

    def test_renaming_keeps_the_versions(self, intake_client: TestClient) -> None:
        template = _create(intake_client)

        response = intake_client.patch(
            f"{BASE}/{template['id']}", json={"name": "New patient intake"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "New patient intake"
        assert len(response.json()["versions"]) == 1

    def test_archiving_hides_it_from_the_list_without_deleting_it(
        self, intake_client: TestClient
    ) -> None:
        template = _create(intake_client)

        intake_client.patch(f"{BASE}/{template['id']}", json={"archived": True})

        assert intake_client.get(BASE).json() == []
        assert intake_client.get(f"{BASE}?include_archived=true").json()[0]["id"] == template["id"]
        assert intake_client.get(f"{BASE}/{template['id']}").status_code == 200

    def test_an_unknown_form_is_404(self, intake_client: TestClient) -> None:
        assert intake_client.get(f"{BASE}/no-such-form").status_code == 404

    def test_an_unexpected_field_is_refused(self, intake_client: TestClient) -> None:
        assert intake_client.post(BASE, json={"name": "X", "published": True}).status_code == 422


class TestItems:
    def test_saving_the_list_keeps_its_order(self, intake_client: TestClient) -> None:
        template = _create(intake_client)

        response = _items(
            intake_client,
            template["id"],
            _draft_id(template),
            [
                {"key": "about", "item_type": "section", "config": {"title": "About you"}},
                {"key": "reason", "item_type": "reason"},
                {"key": "phq9", "item_type": "instrument", "config": {"code": "phq9"}},
            ],
        )

        assert response.status_code == 200, response.text
        keys = [i["key"] for i in response.json()["items"]]
        assert keys == ["about", "reason", "phq9"]
        assert [i["position"] for i in response.json()["items"]] == [0, 1, 2]

    def test_saving_again_replaces_the_whole_list(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)

        response = _items(
            intake_client, template["id"], version_id, [{"key": "reason", "item_type": "reason"}]
        )

        assert [i["key"] for i in response.json()["items"]] == ["reason"]

    def test_a_draft_may_hold_a_half_filled_question(self, intake_client: TestClient) -> None:
        """Saving is how a practice works; only publishing insists it is done."""
        template = _create(intake_client)

        response = _items(
            intake_client,
            template["id"],
            _draft_id(template),
            [{"key": "how_bad", "item_type": "scale", "config": {}}],
        )

        assert response.status_code == 200, response.text

    def test_a_question_comes_back_in_the_words_it_was_saved_in(
        self, intake_client: TestClient
    ) -> None:
        template = _create(intake_client)

        response = _items(
            intake_client,
            template["id"],
            _draft_id(template),
            [
                {
                    "key": "sleep",
                    "item_type": "free_text",
                    "label": "How have you been sleeping?",
                    "help_text": "A sentence or two is plenty.",
                }
            ],
        )

        item = response.json()["items"][0]
        assert item["label"] == "How have you been sleeping?"
        assert item["help_text"] == "A sentence or two is plenty."

    def test_a_question_nobody_has_written_yet_comes_back_unset(
        self, intake_client: TestClient
    ) -> None:
        """Both are absent on the engine's own questions, and on a new one."""
        template = _create(intake_client)

        response = _items(
            intake_client,
            template["id"],
            _draft_id(template),
            [{"key": "reason", "item_type": "reason"}],
        )

        item = response.json()["items"][0]
        assert item["label"] is None
        assert item["help_text"] is None

    def test_a_cleared_box_is_stored_as_unwritten(self, intake_client: TestClient) -> None:
        """Otherwise a label a practice emptied would read as wording."""
        template = _create(intake_client)

        response = _items(
            intake_client,
            template["id"],
            _draft_id(template),
            [{"key": "sleep", "item_type": "free_text", "label": "   ", "help_text": ""}],
        )

        item = response.json()["items"][0]
        assert item["label"] is None
        assert item["help_text"] is None

    def test_a_version_from_another_form_is_404(self, intake_client: TestClient) -> None:
        """A version id is not a capability — it has to belong to the form."""
        mine = _create(intake_client, "Mine")
        theirs = _create(intake_client, "Theirs")

        response = _items(intake_client, mine["id"], _draft_id(theirs), _DEFAULT_ITEMS)

        assert response.status_code == 404

    def test_an_unknown_version_is_404(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        assert _items(intake_client, template["id"], "no-such-version", []).status_code == 404


class TestPublishing:
    def _publish(self, client: TestClient, template_id: str, version_id: str) -> Any:
        return client.post(f"{BASE}/{template_id}/versions/{version_id}/publish")

    def test_publishing_freezes_the_version(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)

        response = self._publish(intake_client, template["id"], version_id)

        assert response.status_code == 200, response.text
        assert response.json()["published_at"] is not None

    def test_editing_a_published_version_is_409(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)
        self._publish(intake_client, template["id"], version_id)

        response = _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)

        assert response.status_code == 409
        assert "new version" in response.json()["error"]["message"]

    def test_publishing_twice_is_409(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)
        self._publish(intake_client, template["id"], version_id)

        assert self._publish(intake_client, template["id"], version_id).status_code == 409

    def test_an_empty_form_is_422(self, intake_client: TestClient) -> None:
        template = _create(intake_client)

        response = self._publish(intake_client, template["id"], _draft_id(template))

        assert response.status_code == 422
        assert "at least one question" in response.json()["error"]["message"]

    def test_a_broken_question_is_422_and_names_the_item(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(
            intake_client,
            template["id"],
            version_id,
            [
                {
                    "key": "how_bad",
                    "item_type": "scale",
                    "label": "How bad has it been?",
                    "config": {"min": 9, "max": 1, "min_label": "a", "max_label": "b"},
                }
            ],
        )

        response = self._publish(intake_client, template["id"], version_id)

        assert response.status_code == 422
        assert response.json()["error"]["message"].startswith("how_bad:")

    @pytest.mark.parametrize(
        ("item_type", "config"),
        [
            ("free_text", {}),
            (
                "single_choice",
                {"options": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]},
            ),
            ("yes_no", {}),
            ("number", {}),
            ("date", {}),
        ],
    )
    def test_a_question_with_nothing_to_ask_is_422_and_names_it(
        self, intake_client: TestClient, item_type: str, config: dict
    ) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(
            intake_client,
            template["id"],
            version_id,
            [{"key": "mood", "item_type": item_type, "config": config}],
        )

        response = self._publish(intake_client, template["id"], version_id)

        assert response.status_code == 422
        assert response.json()["error"]["message"].startswith("mood:")

    @pytest.mark.parametrize(
        ("item_type", "config"),
        [
            ("section", {"title": "About you"}),
            ("instructions", {"body_markdown": "Take your time."}),
            ("demographics", {}),
            ("reason", {}),
            ("instrument", {"code": "phq9"}),
        ],
    )
    def test_the_engine_words_its_own_questions_and_publishes_without_one(
        self, intake_client: TestClient, item_type: str, config: dict
    ) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(
            intake_client,
            template["id"],
            version_id,
            [{"key": "q", "item_type": item_type, "config": config}],
        )

        response = self._publish(intake_client, template["id"], version_id)

        assert response.status_code == 200, response.text

    def test_a_forward_reference_is_422(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(
            intake_client,
            template["id"],
            version_id,
            [
                {
                    "key": "follow_up",
                    "item_type": "free_text",
                    "label": "Tell us more",
                    "config": {"visible_when": {"item_key": "screener", "op": "answered"}},
                },
                {"key": "screener", "item_type": "yes_no", "label": "Have you been seen before?"},
            ],
        )

        response = self._publish(intake_client, template["id"], version_id)

        assert response.status_code == 422
        assert "nothing earlier on this form" in response.json()["error"]["message"]

    def test_publishing_is_on_the_record(
        self, intake_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(intake_client, template["id"], version_id, _DEFAULT_ITEMS)

        self._publish(intake_client, template["id"], version_id)

        published = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.INTAKE_TEMPLATE_PUBLISHED.value
        ]
        assert len(published) == 1
        assert published[0].resource_id == version_id
        assert published[0].changes["item_count"] == 2
        assert published[0].changes["version"] == 1

    def test_the_audit_row_carries_no_questions(
        self, intake_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        template = _create(intake_client)
        version_id = _draft_id(template)
        _items(
            intake_client,
            template["id"],
            version_id,
            [{"key": "reason", "item_type": "instructions", "config": {"body_markdown": "Hello"}}],
        )

        self._publish(intake_client, template["id"], version_id)

        published = next(
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.INTAKE_TEMPLATE_PUBLISHED.value
        )
        assert "Hello" not in str(published.changes)
        assert set(published.changes) == {"template_id", "version", "item_count"}

    def test_a_draft_is_not_audited(
        self, intake_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        """Drafts change all day; only what went live is worth a row."""
        template = _create(intake_client)
        _items(intake_client, template["id"], _draft_id(template), _DEFAULT_ITEMS)

        assert _entries(mock_audit_service) == []


class TestNewVersions:
    def test_a_new_version_copies_the_published_questions(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        first = _draft_id(template)
        _items(intake_client, template["id"], first, _DEFAULT_ITEMS)
        intake_client.post(f"{BASE}/{template['id']}/versions/{first}/publish")

        response = intake_client.post(f"{BASE}/{template['id']}/versions")

        assert response.status_code == 201, response.text
        assert response.json()["version"] == 2
        assert response.json()["published_at"] is None
        assert [i["key"] for i in response.json()["items"]] == ["demographics", "reason"]

    def test_the_published_version_still_reads_as_it_was(self, intake_client: TestClient) -> None:
        template = _create(intake_client)
        first = _draft_id(template)
        _items(intake_client, template["id"], first, _DEFAULT_ITEMS)
        intake_client.post(f"{BASE}/{template['id']}/versions/{first}/publish")
        draft = intake_client.post(f"{BASE}/{template['id']}/versions").json()

        _items(
            intake_client, template["id"], draft["id"], [{"key": "reason", "item_type": "reason"}]
        )

        frozen = intake_client.get(f"{BASE}/{template['id']}/versions/{first}").json()
        assert [i["key"] for i in frozen["items"]] == ["demographics", "reason"]
