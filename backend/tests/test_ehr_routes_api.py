# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for EHR navigation route endpoints."""

from typing import Any
from unittest.mock import patch

from app.models.ehr_route import EhrRoute, EhrRouteStep
from app.repositories import InMemoryEhrRouteRepository
from fastapi import HTTPException, status

# ============================================================================
# Helpers
# ============================================================================


def _seed_simplepractice_route(mock_ehr_route_repo: InMemoryEhrRouteRepository) -> None:
    """Seed a SimplePractice route for testing."""
    route = EhrRoute(
        id="simplepractice",
        ehr_system="simplepractice",
        route_name="navigate_to_soap_entry",
        steps=[
            EhrRouteStep(
                action="click",
                selector="nav[aria-label='Clients']",
                a11y_fingerprint="AXGroup | Navigation",
                intent="find_patient_list",
                dynamic_key=None,
            ),
            EhrRouteStep(
                action="click",
                selector="tr:contains('{patient_name}')",
                a11y_fingerprint="AXTable | Client List",
                intent="find_patient_row",
                dynamic_key="patient_name",
            ),
            EhrRouteStep(
                action="click",
                selector="button:contains('New Note')",
                a11y_fingerprint="AXGroup | Notes Tab",
                intent="find_soap_form",
                dynamic_key=None,
            ),
        ],
        success_count=847,
        last_success="2026-03-23T17:00:00Z",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-03-23T17:00:00Z",
    )
    mock_ehr_route_repo.seed(route)


# ============================================================================
# Happy Path Tests
# ============================================================================


def test_get_ehr_route_success(
    client: Any, mock_ehr_route_repo: InMemoryEhrRouteRepository
) -> None:
    _seed_simplepractice_route(mock_ehr_route_repo)

    response = client.get("/api/ehr-routes/simplepractice")

    assert response.status_code == 200
    data = response.json()
    assert data["ehr_system"] == "simplepractice"
    assert data["route_name"] == "navigate_to_soap_entry"
    assert len(data["steps"]) == 3
    assert data["steps"][0]["action"] == "click"
    assert data["steps"][0]["intent"] == "find_patient_list"
    assert data["steps"][1]["dynamic_key"] == "patient_name"
    assert data["steps"][2]["dynamic_key"] is None
    assert data["success_count"] == 847
    assert data["last_success"] == "2026-03-23T17:00:00Z"


def test_update_ehr_route_step_success(
    client: Any, mock_ehr_route_repo: InMemoryEhrRouteRepository
) -> None:
    _seed_simplepractice_route(mock_ehr_route_repo)

    response = client.patch(
        "/api/ehr-routes/simplepractice/steps/0",
        json={
            "selector": "div.sidebar a[href='/clients']",
            "a11y_fingerprint": "AXGroup | Sidebar Navigation",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["steps"][0]["selector"] == "div.sidebar a[href='/clients']"
    assert data["steps"][0]["a11y_fingerprint"] == "AXGroup | Sidebar Navigation"
    # Other fields unchanged
    assert data["steps"][0]["action"] == "click"
    assert data["steps"][0]["intent"] == "find_patient_list"


def test_ehr_navigate_success(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "sessions_health",
            "goal": "Navigate to SOAP note for 8:00 PM on March 23, 2026",
            "current_url": "https://app.sessionshealth.com/",
            "dom_snapshot": "<a href='/events/123-260323'>[PATIENT] 8:00pm</a>",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] in ("click", "navigate", "wait", "fill", "none")
    assert "selector" in data
    assert "reasoning" in data
    assert 0.0 <= data["confidence"] <= 1.0
    assert "is_on_target_page" in data


def test_ehr_navigate_with_previous_actions(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "sessions_health",
            "goal": "Navigate to SOAP note for 8:00 PM on March 23, 2026",
            "current_url": "https://app.sessionshealth.com/calendar?date=2026-03-23",
            "dom_snapshot": "<div class='fc-event'>[PATIENT] 8:00pm</div>",
            "previous_actions": [
                {
                    "action": "navigate",
                    "target": "/calendar?date=2026-03-23&view=day",
                    "result": "Loaded calendar day view",
                },
            ],
            "failed_action": "click .fc-event — element not clickable",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["action"] in ("click", "navigate", "wait", "fill", "none")


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_get_ehr_route_not_found(client: Any) -> None:
    response = client.get("/api/ehr-routes/simplepractice")

    assert response.status_code == 404
    assert "No route found" in response.json()["detail"]


def test_get_ehr_route_invalid_system(client: Any) -> None:
    response = client.get("/api/ehr-routes/invalid_system")

    assert response.status_code == 422


def test_update_step_route_not_found(client: Any) -> None:
    response = client.patch(
        "/api/ehr-routes/simplepractice/steps/0",
        json={
            "selector": "div.new",
            "a11y_fingerprint": "AXGroup | New",
        },
    )

    assert response.status_code == 404


def test_update_step_index_out_of_range(
    client: Any, mock_ehr_route_repo: InMemoryEhrRouteRepository
) -> None:
    _seed_simplepractice_route(mock_ehr_route_repo)

    response = client.patch(
        "/api/ehr-routes/simplepractice/steps/99",
        json={
            "selector": "div.new",
            "a11y_fingerprint": "AXGroup | New",
        },
    )

    assert response.status_code == 422
    assert "out of range" in response.json()["detail"]


def test_ehr_navigate_rate_limit_exceeded(client: Any) -> None:
    """Rate limit should kick in after 50 calls/user/day."""
    with patch(
        "app.routes.ehr_routes.get_ehr_navigate_limiter"
    ) as mock_limiter:

        def raise_429(key: str) -> None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )

        mock_window = mock_limiter.return_value
        mock_window.check.side_effect = raise_429

        response = client.post(
            "/api/ehr-navigate",
            json={
                "ehr_system": "sessions_health",
                "goal": "Navigate to SOAP note for 8:00 PM on March 23, 2026",
                "current_url": "https://app.sessionshealth.com/",
                "dom_snapshot": "<a href='/events/123-260323'>[PATIENT] 8:00pm</a>",
            },
        )

        assert response.status_code == 429


# ============================================================================
# Validation Tests
# ============================================================================


def test_ehr_navigate_invalid_ehr_system(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "not_an_ehr",
            "goal": "Navigate to SOAP note",
            "current_url": "https://example.com/",
            "dom_snapshot": "<div>page</div>",
        },
    )

    assert response.status_code == 422


def test_ehr_navigate_dom_snapshot_too_long(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "sessions_health",
            "goal": "Navigate to SOAP note",
            "current_url": "https://app.sessionshealth.com/",
            "dom_snapshot": "x" * 50_001,
        },
    )

    assert response.status_code == 422


def test_ehr_navigate_missing_required_fields(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={"ehr_system": "sessions_health"},
    )

    assert response.status_code == 422


def test_ehr_navigate_goal_too_long(client: Any) -> None:
    response = client.post(
        "/api/ehr-navigate",
        json={
            "ehr_system": "sessions_health",
            "goal": "x" * 501,
            "current_url": "https://app.sessionshealth.com/",
            "dom_snapshot": "<div>page</div>",
        },
    )

    assert response.status_code == 422


# ============================================================================
# Route Learning Tests
# ============================================================================


def test_update_step_preserves_unchanged_fields(
    client: Any, mock_ehr_route_repo: InMemoryEhrRouteRepository
) -> None:
    _seed_simplepractice_route(mock_ehr_route_repo)

    client.patch(
        "/api/ehr-routes/simplepractice/steps/1",
        json={
            "selector": "td.patient-name:contains('{patient_name}')",
            "a11y_fingerprint": "AXCell | Patient Name",
        },
    )

    route = mock_ehr_route_repo.get("simplepractice")
    assert route is not None
    # Updated fields
    assert route.steps[1].selector == "td.patient-name:contains('{patient_name}')"
    assert route.steps[1].a11y_fingerprint == "AXCell | Patient Name"
    # Preserved fields
    assert route.steps[1].action == "click"
    assert route.steps[1].intent == "find_patient_row"
    assert route.steps[1].dynamic_key == "patient_name"
    # Other steps unchanged
    assert route.steps[0].selector == "nav[aria-label='Clients']"
    assert route.steps[2].selector == "button:contains('New Note')"
