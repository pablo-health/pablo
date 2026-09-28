# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""A practice's own invitation wording, and the preview of one client's email.

The claim worth the most here is the last one: the preview a clinician reads
before pressing Send is the email that goes out, with only the link withheld.
Both are built by one function, and these tests hold them to it by sending an
invitation and comparing it to the preview taken just before.

Mounts the real routers on a fresh app with auth, the stores and both delivery
channels overridden, like ``test_portal_auth_routes.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import get_current_user, require_active_subscription
from app.models.audit import AuditAction
from app.portal import invite_template_routes, routes
from app.portal.adapters import INVITE_BODY, INVITE_SUBJECT
from app.portal.clinicians import get_primary_clinician_name
from app.portal.delivery import (
    CapturingInviteDelivery,
    CapturingRenderedInviteDelivery,
    FakeSmsGateway,
)
from app.portal.factory import get_invite_delivery, get_sms_gateway
from app.portal.invite_composer import InviteFacts, compose, get_invite_form_names
from app.portal.invite_email import (
    DEFAULT_TEMPLATE,
    PLACEHOLDERS,
    PREVIEW_LINK,
    InviteContext,
    InviteTemplate,
    describe_duration,
    render,
    template_problems,
)
from app.portal.invite_template_store import (
    InMemoryInviteTemplateStore,
    get_invite_template_store,
)
from app.portal.practice_routes import PracticeAddress
from app.portal.store import InMemoryPortalAuthStore, InMemoryPortalSessionStore
from app.portal.tenant_gateway import PortalStores, get_portal_stores
from app.repositories import get_patient_repository
from app.services.audit_service import get_audit_service
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

TENANT = "practice_abc123"
PRACTICE_ID = "practice-1"
PRACTICE_NAME = "Example Therapy"
PATIENT_ID = "11111111-1111-4111-8111-111111111111"
FORM_VERSION = "version-1"

CUSTOM = InviteTemplate(
    subject="{{practice_name}}: your forms",
    body=(
        "Hi {{client_first_name}},\n\n"
        "Please fill in:\n{{forms}}\n\n"
        "Sign in here: {{portal_link}}\n"
        "The link works for {{link_expiry}}."
    ),
)


class _FakePatient:
    id = PATIENT_ID
    first_name = "Robin"
    email = "robin@example.test"
    phone = "+15005550006"


class _FakePatientRepository:
    def get(self, patient_id: str, user_id: str) -> Any:
        return _FakePatient() if patient_id == PATIENT_ID else None


class _RecordingAudit:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def log(self, action: Any, *_args: Any, **_kwargs: Any) -> None:
        self.actions.append(str(action))


def _forms(_patient_id: str, _user_id: str, upcoming: Any) -> list[str]:
    """One form already out, plus whatever is about to be sent."""
    names = ["Consent to treatment"]
    if FORM_VERSION in list(upcoming):
        names.append("Intake questionnaire")
    return names


# ── rendering and checking a template ───────────────────────────────────


def test_the_default_template_is_valid_and_worded_as_designed() -> None:
    assert template_problems(DEFAULT_TEMPLATE) == []
    assert DEFAULT_TEMPLATE.subject == "{{clinician_name}} invited you to your patient portal"
    assert DEFAULT_TEMPLATE.body == (
        "{{clinician_name}} has invited you to the patient portal for {{practice_name}}.\n\n"
        "Use this link to sign in:\n\n"
        "{{portal_link}}\n\n"
        "When you open the link, we'll text a code to your phone. "
        "The link works for {{link_expiry}}."
    )
    # No greeting by default: a practice adds one if it wants it.
    assert "{{client_first_name}}" not in DEFAULT_TEMPLATE.body


def test_the_clinicians_name_is_a_placeholder_the_editor_offers() -> None:
    assert PLACEHOLDERS["clinician_name"] == "Your name"
    greeting = InviteTemplate(
        subject="A note from {{clinician_name}}",
        body="Hi {{client_first_name}},\n\n{{clinician_name}} here.\n\n{{portal_link}}",
    )
    assert template_problems(greeting) == []


def _default_for(clinician_name: str | None) -> tuple[str, str]:
    rendered = compose(
        None,
        InviteFacts(
            client_first_name="Robin",
            practice_name=PRACTICE_NAME,
            forms=[],
            clinician_name=clinician_name,
        ),
        "https://portal.example.test/portal/example-therapy#invite=t",
    )
    return rendered.subject, rendered.text


def test_the_default_names_the_clients_clinician() -> None:
    subject, text = _default_for("Dr. Jane Smith")

    assert subject == "Dr. Jane Smith invited you to your patient portal"
    assert text.startswith(
        "Dr. Jane Smith has invited you to the patient portal for Example Therapy.\n\n"
    )


@pytest.mark.parametrize("missing", [None, "", "   "], ids=["none", "empty", "blank"])
def test_with_no_clinicians_name_the_practice_stands_in(missing: str | None) -> None:
    subject, text = _default_for(missing)

    assert subject == "Example Therapy invited you to your patient portal"
    assert text.startswith(
        "Example Therapy has invited you to the patient portal for Example Therapy."
    )


@pytest.mark.parametrize("clinician", ["Jane Smith", None])
def test_the_default_never_leaves_a_placeholder_or_an_empty_name(clinician: str | None) -> None:
    subject, text = _default_for(clinician)

    for rendered in (subject, text):
        assert "{{" not in rendered
        assert "}}" not in rendered
        assert not rendered.startswith(" ")
    assert " invited you" in subject
    assert "for ." not in text


def test_the_fixed_wording_names_nobody_and_leaves_nothing_unfilled() -> None:
    """``send_invite`` has only the link to go on, so its wording names no
    one — and never an empty name where the default would put one."""
    for fixed in (INVITE_SUBJECT, INVITE_BODY.format(link="https://portal.example.test/x")):
        assert "{{" not in fixed
        assert "invited you" not in fixed


def test_render_fills_every_placeholder() -> None:
    rendered = render(
        CUSTOM,
        InviteContext(
            portal_link="https://portal.example.test/x/redeem#t",
            client_first_name="Robin",
            practice_name=PRACTICE_NAME,
            forms=["Consent to treatment", "Intake questionnaire"],
            link_expiry="15 minutes",
        ),
    )
    assert rendered.subject == "Example Therapy: your forms"
    assert rendered.text == (
        "Hi Robin,\n\n"
        "Please fill in:\n- Consent to treatment\n- Intake questionnaire\n\n"
        "Sign in here: https://portal.example.test/x/redeem#t\n"
        "The link works for 15 minutes."
    )


@pytest.mark.parametrize(
    ("template", "problem"),
    [
        (InviteTemplate(subject="Hello", body="No link here."), "Include {{portal_link}}"),
        (InviteTemplate(subject="", body="{{portal_link}}"), "Add a subject."),
        (
            InviteTemplate(subject="{{portal_link}}", body="{{portal_link}}"),
            "not the subject",
        ),
        (
            InviteTemplate(subject="Hi", body="{{portal_link}} {{diagnosis}}"),
            "{{diagnosis}}",
        ),
        (InviteTemplate(subject="Two\nlines", body="{{portal_link}}"), "one line"),
    ],
)
def test_a_template_that_cannot_be_sent_says_why(template: InviteTemplate, problem: str) -> None:
    assert any(problem in p for p in template_problems(template))


def test_the_default_wording_says_when_the_code_arrives() -> None:
    """The code is texted when the link is opened, so the email must not say
    it has already been sent."""
    rendered = render(
        DEFAULT_TEMPLATE,
        InviteContext(
            portal_link="https://portal.example.test/x#invite=t",
            client_first_name="",
            practice_name="",
            forms=[],
            link_expiry="7 days",
        ),
    )
    assert "When you open the link, we'll text a code to your phone." in rendered.text
    assert "texted you" not in rendered.text
    assert "The link works for 7 days." in rendered.text


def test_describe_duration() -> None:
    assert describe_duration(900) == "15 minutes"
    assert describe_duration(3600) == "1 hour"
    assert describe_duration(7200) == "2 hours"
    assert describe_duration(60) == "1 minute"
    assert describe_duration(86_400) == "1 day"
    assert describe_duration(604_800) == "7 days"
    # Not a whole number of days: stays in hours rather than rounding.
    assert describe_duration(129_600) == "36 hours"


# ── the routes ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _portal_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", "template-test-signing-key-not-a-real-secret")
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", "https://portal.example.test")
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _practice(monkeypatch: pytest.MonkeyPatch) -> None:
    address = PracticeAddress(slug="example-therapy", display_name=PRACTICE_NAME, enabled=True)
    for module in (routes, invite_template_routes):
        monkeypatch.setattr(
            module, "_resolve_practice_from_email", lambda _email: (PRACTICE_ID, TENANT)
        )
        monkeypatch.setattr(module, "ensure_practice_slug", lambda _practice_id: address)


@pytest.fixture
def templates() -> InMemoryInviteTemplateStore:
    return InMemoryInviteTemplateStore()


@pytest.fixture
def audit() -> _RecordingAudit:
    return _RecordingAudit()


def _app(
    user: User,
    delivery: CapturingInviteDelivery,
    templates: InMemoryInviteTemplateStore,
    audit: _RecordingAudit,
) -> TestClient:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(routes.router)
    application.include_router(invite_template_routes.router)
    stores = PortalStores(
        challenges=InMemoryPortalAuthStore(), sessions=InMemoryPortalSessionStore(), tenant=TENANT
    )
    overrides = application.dependency_overrides
    overrides[get_current_user] = lambda: user
    overrides[require_active_subscription] = lambda: user
    overrides[get_patient_repository] = _FakePatientRepository
    overrides[get_portal_stores] = lambda: stores
    overrides[get_audit_service] = lambda: audit
    overrides[get_invite_delivery] = lambda: delivery
    overrides[get_sms_gateway] = FakeSmsGateway
    overrides[get_invite_template_store] = lambda: templates
    overrides[get_invite_form_names] = lambda: _forms
    overrides[get_primary_clinician_name] = lambda: _primary_clinician
    return TestClient(application)


#: The chart's primary clinician, by patient id. Anyone not here has none.
PRIMARY_CLINICIANS: dict[str, str] = {}


def _primary_clinician(patient_id: str) -> str | None:
    return PRIMARY_CLINICIANS.get(patient_id)


@pytest.fixture(autouse=True)
def _patient_has_a_primary_clinician() -> Iterator[None]:
    PRIMARY_CLINICIANS[PATIENT_ID] = "Jane Smith"
    yield
    PRIMARY_CLINICIANS.clear()


@pytest.fixture
def rendered_delivery() -> CapturingRenderedInviteDelivery:
    return CapturingRenderedInviteDelivery()


@pytest.fixture
def client(
    mock_user: User,
    rendered_delivery: CapturingRenderedInviteDelivery,
    templates: InMemoryInviteTemplateStore,
    audit: _RecordingAudit,
) -> TestClient:
    return _app(mock_user, rendered_delivery, templates, audit)


@pytest.fixture
def fixed_client(
    mock_user: User, templates: InMemoryInviteTemplateStore, audit: _RecordingAudit
) -> TestClient:
    return _app(mock_user, CapturingInviteDelivery(), templates, audit)


def test_a_practice_starts_on_the_default_wording(client: TestClient) -> None:
    body = client.get("/api/portal/invite-template").json()
    assert body["editable"] is True
    assert body["is_default"] is True
    assert body["subject"] == DEFAULT_TEMPLATE.subject
    assert body["body"] == DEFAULT_TEMPLATE.body
    required = [p["name"] for p in body["placeholders"] if p["required"]]
    assert required == ["portal_link"]


def test_saved_wording_is_read_back_and_can_be_reset(client: TestClient) -> None:
    saved = client.put(
        "/api/portal/invite-template", json={"subject": CUSTOM.subject, "body": CUSTOM.body}
    )
    assert saved.status_code == 200
    assert saved.json()["is_default"] is False
    assert client.get("/api/portal/invite-template").json()["subject"] == CUSTOM.subject

    reset = client.delete("/api/portal/invite-template").json()
    assert reset["is_default"] is True
    assert client.get("/api/portal/invite-template").json()["body"] == DEFAULT_TEMPLATE.body


def test_wording_without_the_link_is_refused_with_the_reason(
    client: TestClient, templates: InMemoryInviteTemplateStore
) -> None:
    response = client.put(
        "/api/portal/invite-template", json={"subject": "Hello", "body": "Come see us."}
    )
    assert response.status_code == 422
    assert any("{{portal_link}}" in p for p in response.json()["error"]["details"]["problems"])
    assert templates.templates == {}


def test_fixed_wording_deployments_offer_no_editor(
    fixed_client: TestClient, templates: InMemoryInviteTemplateStore
) -> None:
    assert fixed_client.get("/api/portal/invite-template").json()["editable"] is False
    response = fixed_client.put(
        "/api/portal/invite-template", json={"subject": CUSTOM.subject, "body": CUSTOM.body}
    )
    assert response.status_code == 409
    assert templates.templates == {}


def test_the_editor_preview_uses_an_example_client(client: TestClient) -> None:
    body = client.post(
        "/api/portal/invite-template/preview",
        json={"subject": CUSTOM.subject, "body": CUSTOM.body},
    ).json()
    assert body["problems"] == []
    assert body["subject"] == "Example Therapy: your forms"
    assert "Hi Alex," in body["text"]
    assert PREVIEW_LINK in body["text"]


def test_the_editor_preview_fills_the_clinicians_name_with_an_example(
    client: TestClient,
) -> None:
    body = client.post(
        "/api/portal/invite-template/preview",
        json={"subject": DEFAULT_TEMPLATE.subject, "body": DEFAULT_TEMPLATE.body},
    ).json()

    assert body["problems"] == []
    assert body["subject"] == "Jordan Rivera invited you to your patient portal"
    assert body["text"].startswith(
        "Jordan Rivera has invited you to the patient portal for Example Therapy."
    )


def test_the_editor_lists_the_clinicians_name(client: TestClient) -> None:
    placeholders = client.get("/api/portal/invite-template").json()["placeholders"]

    assert {"name": "clinician_name", "label": "Your name", "required": False} in placeholders


def test_the_default_invitation_names_the_primary_clinician(
    client: TestClient, rendered_delivery: CapturingRenderedInviteDelivery
) -> None:
    assert client.post(f"/api/patients/{PATIENT_ID}/portal-invite").status_code == 202

    [email] = rendered_delivery.sent
    assert email.subject == "Jane Smith invited you to your patient portal"
    assert email.text is not None
    assert email.text.startswith(
        "Jane Smith has invited you to the patient portal for Example Therapy."
    )


def test_with_no_primary_clinician_the_invitation_names_the_practice(
    client: TestClient, rendered_delivery: CapturingRenderedInviteDelivery
) -> None:
    PRIMARY_CLINICIANS.clear()

    assert client.post(f"/api/patients/{PATIENT_ID}/portal-invite").status_code == 202

    [email] = rendered_delivery.sent
    assert email.subject == "Example Therapy invited you to your patient portal"
    assert email.text is not None
    assert "{{" not in email.text


def test_a_saved_custom_template_is_sent_as_the_practice_wrote_it(
    client: TestClient, rendered_delivery: CapturingRenderedInviteDelivery
) -> None:
    client.put("/api/portal/invite-template", json={"subject": CUSTOM.subject, "body": CUSTOM.body})

    assert client.post(f"/api/patients/{PATIENT_ID}/portal-invite").status_code == 202

    [email] = rendered_delivery.sent
    assert email.subject == "Example Therapy: your forms"
    assert email.text is not None
    assert email.text.startswith("Hi Robin,\n\nPlease fill in:")
    assert "invited you" not in email.text


def test_the_client_preview_is_the_email_that_is_sent_with_the_link_withheld(
    client: TestClient,
    rendered_delivery: CapturingRenderedInviteDelivery,
    audit: _RecordingAudit,
) -> None:
    client.put("/api/portal/invite-template", json={"subject": CUSTOM.subject, "body": CUSTOM.body})

    # Previewed at the review step, with a form about to go out.
    preview = client.post(
        f"/api/patients/{PATIENT_ID}/portal-invite/preview",
        json={"version_ids": [FORM_VERSION]},
    ).json()
    assert preview["available"] is True
    assert preview["to_email"] == "robin@example.test"
    assert "Hi Robin," in preview["text"]
    assert "- Intake questionnaire" in preview["text"]
    assert str(AuditAction.PATIENT_PORTAL_INVITE_PREVIEWED) in audit.actions

    sent = client.post(f"/api/patients/{PATIENT_ID}/portal-invite")
    assert sent.status_code == 202
    [email] = rendered_delivery.sent
    assert email.link.startswith("https://portal.example.test/")
    assert email.subject == preview["subject"]
    # The fake lookup has no assignment store behind it, so the send sees
    # only the form already out; compare against a preview asked the same
    # way. The real ordering — assign, then invite — is walked end to end
    # in frontend/e2e/specs/new-client-intake.spec.ts.
    same_moment = client.post(
        f"/api/patients/{PATIENT_ID}/portal-invite/preview", json={"version_ids": []}
    ).json()
    assert email.text == same_moment["text"].replace(PREVIEW_LINK, email.link)
    assert email.link not in str(sent.json())


def test_fixed_wording_deployments_offer_no_client_preview(
    fixed_client: TestClient, audit: _RecordingAudit
) -> None:
    body = fixed_client.post(
        f"/api/patients/{PATIENT_ID}/portal-invite/preview", json={"version_ids": []}
    ).json()
    assert body == {"available": False, "to_email": None, "subject": None, "text": None}
    assert audit.actions == []


def test_a_client_preview_for_an_unknown_chart_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/patients/99999999-9999-4999-8999-999999999999/portal-invite/preview",
        json={"version_ids": []},
    )
    assert response.status_code == 404
