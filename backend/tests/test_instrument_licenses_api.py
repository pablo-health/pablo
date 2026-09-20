# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the instrument catalogue and its licences.

Four things are being checked that the service alone cannot show: the door,
the shape of the catalogue both screens read, the status codes, and the
audit rows.

**The door.** These are ordinary clinician routes, so an unauthenticated
caller is a 401. Nothing here belongs to a patient, and nothing here is
reachable with a patient's credential.

**The catalogue.** One list serves the settings screen and the form
builder, which is why ``attested`` and ``can_ask_on_a_form`` are separate
fields: an instrument can be licensed and still not be one this engine
carries the wording for.

**The status codes.** A 422 means permission is not a thing that can be
recorded for this instrument — unknown, free to use already, or a form the
engine does not have. A 404 means there was nothing in force to withdraw.

**The audit rows.** Recording permission and withdrawing it are both on the
record, because together they say what a form published afterwards was
allowed to ask. The payload is the instrument code and nothing else; the
licence reference is the practice's own note and stays on the row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models.audit import AuditAction
from app.repositories import InMemoryInstrumentLicenseRepository
from app.routes.instrument_licenses import get_instrument_license_service
from app.services.instrument_license_service import InstrumentLicenseService
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.services import AuditService

BASE = "/api/intake/instruments"

#: One of the registry's use-restricted measures. Any would do; this one is
#: named so a failure points at a row a reader can go and look at.
RESTRICTED = "cssrs"

#: One the registry calls public_domain, so there is nothing to attest.
FREE = "phq9"

#: One the registry will never carry the wording for.
SOLD = "bdi2"


@pytest.fixture
def license_repo() -> InMemoryInstrumentLicenseRepository:
    return InMemoryInstrumentLicenseRepository()


@pytest.fixture
def instrument_client(
    client: TestClient, license_repo: InMemoryInstrumentLicenseRepository
) -> TestClient:
    """The shared clinician client, with the licence store in memory."""
    app.dependency_overrides[get_instrument_license_service] = lambda: InstrumentLicenseService(
        license_repo
    )
    return client


def _entries(audit: AuditService) -> list[Any]:
    return [call.args[0] for call in audit._repo.append.call_args_list]


def _entry(client: TestClient, code: str) -> dict[str, Any]:
    body = client.get(BASE).json()
    return next(row for row in body if row["code"] == code)


class TestTheDoor:
    def test_without_a_credential_is_401(self) -> None:
        """No overrides here: the real clinician door answers."""
        assert TestClient(app).get(BASE).status_code == 401


class TestTheCatalogue:
    def test_it_lists_every_instrument_the_engine_knows(
        self, instrument_client: TestClient
    ) -> None:
        from app.outcome_measures.instruments import INSTRUMENT_REGISTRY  # noqa: PLC0415

        codes = {row["code"] for row in instrument_client.get(BASE).json()}
        assert codes == set(INSTRUMENT_REGISTRY)

    def test_a_free_measure_is_askable_and_needs_nothing_recorded(
        self, instrument_client: TestClient
    ) -> None:
        row = _entry(instrument_client, FREE)
        assert row["rights"] == "public_domain"
        assert row["can_ask_on_a_form"] is True
        assert row["attested"] is False

    def test_a_restricted_measure_starts_unattested(self, instrument_client: TestClient) -> None:
        row = _entry(instrument_client, RESTRICTED)
        assert row["rights"] == "attestation_required"
        assert row["attested"] is False
        assert row["attested_at"] is None

    def test_a_sold_measure_carries_its_name_and_count_and_no_more(
        self, instrument_client: TestClient
    ) -> None:
        """Enough to recognise it. Never enough to ask it."""
        row = _entry(instrument_client, SOLD)
        assert row["rights"] == "never_ship"
        assert row["can_ask_on_a_form"] is False
        assert row["display_name"]
        assert row["item_count"] > 0

    def test_every_entry_says_what_the_restriction_is(self, instrument_client: TestClient) -> None:
        for row in instrument_client.get(BASE).json():
            assert row["rights_note"]


class TestRecordingPermission:
    def test_it_turns_the_instrument_attested(self, instrument_client: TestClient) -> None:
        response = instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})

        assert response.status_code == 201, response.text
        assert response.json()["instrument_code"] == RESTRICTED
        assert _entry(instrument_client, RESTRICTED)["attested"] is True

    def test_the_practice_can_keep_its_own_reference(self, instrument_client: TestClient) -> None:
        instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation",
            json={"license_reference": "PO-4417", "notes": "Renewed in March."},
        )

        assert _entry(instrument_client, RESTRICTED)["license_reference"] == "PO-4417"

    def test_recording_it_again_replaces_what_was_there(
        self, instrument_client: TestClient
    ) -> None:
        """Still one answer to 'is this licensed here', with a newer record."""
        first = instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation", json={"license_reference": "old"}
        ).json()
        second = instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation", json={"license_reference": "new"}
        ).json()

        assert second["id"] != first["id"]
        assert _entry(instrument_client, RESTRICTED)["license_reference"] == "new"

    def test_a_blank_reference_is_stored_as_unset(self, instrument_client: TestClient) -> None:
        response = instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation", json={"license_reference": "   "}
        )

        assert response.json()["license_reference"] is None

    def test_a_free_measure_has_nothing_to_record(self, instrument_client: TestClient) -> None:
        assert instrument_client.post(f"{BASE}/{FREE}/attestation", json={}).status_code == 422

    def test_a_sold_measure_cannot_be_unlocked_by_attesting(
        self, instrument_client: TestClient
    ) -> None:
        """No permission a practice records makes the engine have the form."""
        assert instrument_client.post(f"{BASE}/{SOLD}/attestation", json={}).status_code == 422

    def test_an_unknown_instrument_is_422(self, instrument_client: TestClient) -> None:
        assert instrument_client.post(f"{BASE}/nonesuch/attestation", json={}).status_code == 422

    def test_an_unexpected_field_is_refused(self, instrument_client: TestClient) -> None:
        response = instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation", json={"approved_by_counsel": True}
        )
        assert response.status_code == 422


class TestWithdrawingPermission:
    def test_it_turns_the_instrument_unattested(self, instrument_client: TestClient) -> None:
        instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})

        response = instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation")

        assert response.status_code == 200, response.text
        assert response.json()["revoked_at"] is not None
        assert _entry(instrument_client, RESTRICTED)["attested"] is False

    def test_withdrawing_nothing_is_404(self, instrument_client: TestClient) -> None:
        assert instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation").status_code == 404

    def test_withdrawing_twice_is_404_the_second_time(self, instrument_client: TestClient) -> None:
        instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})
        instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation")

        assert instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation").status_code == 404

    def test_permission_can_be_recorded_again_afterwards(
        self, instrument_client: TestClient
    ) -> None:
        instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})
        instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation")

        assert (
            instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={}).status_code == 201
        )
        assert _entry(instrument_client, RESTRICTED)["attested"] is True


class TestTheRecord:
    def test_recording_permission_is_audited(
        self, instrument_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})

        rows = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.INSTRUMENT_LICENSE_ATTESTED.value
        ]
        assert len(rows) == 1
        assert rows[0].changes == {"instrument_code": RESTRICTED}

    def test_withdrawing_it_is_audited(
        self, instrument_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        instrument_client.post(f"{BASE}/{RESTRICTED}/attestation", json={})
        instrument_client.delete(f"{BASE}/{RESTRICTED}/attestation")

        rows = [
            e
            for e in _entries(mock_audit_service)
            if e.action == AuditAction.INSTRUMENT_LICENSE_REVOKED.value
        ]
        assert len(rows) == 1
        assert rows[0].changes == {"instrument_code": RESTRICTED}

    def test_the_practices_own_note_stays_off_the_record(
        self, instrument_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        instrument_client.post(
            f"{BASE}/{RESTRICTED}/attestation",
            json={"license_reference": "PO-4417", "notes": "Bought by the office manager."},
        )

        recorded = str(_entries(mock_audit_service))
        assert "PO-4417" not in recorded
        assert "office manager" not in recorded

    def test_reading_the_catalogue_is_not_audited(
        self, instrument_client: TestClient, mock_audit_service: AuditService
    ) -> None:
        """An instrument's rights are a fact about the instrument."""
        instrument_client.get(BASE)

        assert _entries(mock_audit_service) == []
