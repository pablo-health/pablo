# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the user profile PATCH endpoint and provider_type field."""

from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.models import User
from app.repositories import (
    ClinicianProfile,
    InMemoryClinicianProfileRepository,
    InMemoryIdentityRepository,
    InMemoryUserRepository,
)
from app.routes.users import _user_has_totp_factor


class TestUpdateProfile:
    """Test PATCH /api/users/me."""

    def test_set_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"provider_type": "prescriber"})

        assert response.status_code == 200
        assert response.json()["provider_type"] == "prescriber"
        assert mock_user_repo.get(mock_user.id).provider_type == "prescriber"  # type: ignore[union-attr]

    def test_provider_type_accepts_all_three_values(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        for value in ("therapist", "prescriber", "both"):
            response = client.patch("/api/users/me", json={"provider_type": value})
            assert response.status_code == 200
            assert response.json()["provider_type"] == value

    def test_invalid_provider_type_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"provider_type": "physician"})
        assert response.status_code == 422

    def test_partial_update_preserves_unspecified_fields(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.provider_type = "therapist"
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"name": "Dr. Renamed"})

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Dr. Renamed"
        assert body["provider_type"] == "therapist"

    def test_user_status_includes_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes provider_type so the frontend
        can gate the onboarding redirect on it."""
        mock_user.provider_type = "both"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] == "both"

    def test_user_status_provider_type_null_for_new_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A user who has never picked a provider_type returns null,
        which is the onboarding-needed signal."""
        mock_user.provider_type = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] is None


class TestTitleAndCredentials:
    """Test that PATCH /me persists title/credentials onto the per-practice
    ClinicianProfile row, and GET /me/status surfaces them (PABLO-ztv.1).

    Pre-fix, the request schema accepted ``title`` and ``credentials`` but
    they were silently dropped — the inline comment at users.py acknowledged
    the gap. Tests below assert the round-trip works for both an existing
    profile (update path) and a new profile (create path, with the practice
    resolved from the user's email).
    """

    def test_patch_me_updates_title_on_existing_profile(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                title="Mr.",
                credentials="LMFT",
            )
        )

        response = client.patch("/api/users/me", json={"title": "Dr."})

        assert response.status_code == 200
        assert response.json()["title"] == "Dr."
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.title == "Dr."
        # Unspecified field preserved — PATCH, not PUT.
        assert stored.credentials == "LMFT"
        # Practice_id never overwritten by PATCH /me.
        assert stored.practice_id == "practice-abc"

    def test_patch_me_updates_credentials_on_existing_profile(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                title="Dr.",
                credentials=None,
            )
        )

        response = client.patch("/api/users/me", json={"credentials": "PhD, LMFT"})

        assert response.status_code == 200
        assert response.json()["credentials"] == "PhD, LMFT"
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.credentials == "PhD, LMFT"
        assert stored.title == "Dr."

    def test_patch_me_creates_profile_when_practice_resolves(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """First-time onboarding write: no ClinicianProfile row yet, but the
        practice mapping exists, so the upsert creates the row."""
        mock_user_repo.update(mock_user)
        assert mock_clinician_profile_repo.get(mock_user.id) is None

        with patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=("practice-fresh", "practice_fresh"),
        ):
            response = client.patch(
                "/api/users/me",
                json={"title": "Dr.", "credentials": "PsyD"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Dr."
        assert body["credentials"] == "PsyD"
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.practice_id == "practice-fresh"
        assert stored.title == "Dr."
        assert stored.credentials == "PsyD"

    def test_patch_me_skips_profile_when_no_practice_mapping(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """If the user has no practice mapping yet (unusual — the wizard
        should provision the practice before this point), the title/
        credentials write is skipped silently. Other PATCH fields still
        persist so the rest of the onboarding step succeeds."""
        mock_user_repo.update(mock_user)

        with patch(
            "app.auth.service._resolve_practice_from_email",
            return_value=None,
        ):
            response = client.patch(
                "/api/users/me",
                json={"title": "Dr.", "provider_type": "therapist"},
            )

        assert response.status_code == 200
        assert mock_clinician_profile_repo.get(mock_user.id) is None
        stored_user = mock_user_repo.get(mock_user.id)
        assert stored_user is not None
        assert stored_user.provider_type == "therapist"

    def test_patch_me_title_and_credentials_are_independent_fields(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """PATCH semantics: sending only ``title`` must not clear
        ``credentials`` and vice versa."""
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                title="Dr.",
                credentials="LMFT",
            )
        )

        client.patch("/api/users/me", json={"title": "Ms."})
        assert mock_clinician_profile_repo.get(mock_user.id).credentials == "LMFT"  # type: ignore[union-attr]

        client.patch("/api/users/me", json={"credentials": "PsyD"})
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.title == "Ms."
        assert stored.credentials == "PsyD"

    def test_user_status_includes_title_and_credentials(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """GET /me/status surfaces title/credentials from the
        per-practice ClinicianProfile row, so a downstream deployment's
        onboarding wizard and the dashboard layout can render the formal
        name without a second API call."""
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                title="Dr.",
                credentials="PsyD, LMFT",
            )
        )

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Dr."
        assert body["credentials"] == "PsyD, LMFT"

    def test_user_status_title_and_credentials_null_without_profile(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        """A user with no ClinicianProfile row yet (fresh signup) returns
        null for both fields — the wizard treats this as the
        'profile-setup-needed' signal."""
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        body = response.json()
        assert body["title"] is None
        assert body["credentials"] is None

    def test_patch_me_credential_titles_persist_and_derive_display(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """Structured credential_titles persist as the source of truth and
        the legacy ``credentials`` display string is derived (joined) from
        them. Board-cert suffixes are preserved verbatim."""
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
            )
        )

        response = client.patch(
            "/api/users/me",
            json={"credential_titles": ["PMHNP-BC", "RN"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["credential_titles"] == ["PMHNP-BC", "RN"]
        # Display string derived from the structured set.
        assert body["credentials"] == "PMHNP-BC, RN"
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.credential_titles == ["PMHNP-BC", "RN"]
        assert stored.credentials == "PMHNP-BC, RN"

    def test_credential_titles_blanks_dropped_and_trimmed(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(user_id=mock_user.id, practice_id="practice-abc")
        )

        response = client.patch(
            "/api/users/me",
            json={"credential_titles": ["  PMHNP-BC  ", "", "   ", "PhD"]},
        )

        assert response.status_code == 200
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.credential_titles == ["PMHNP-BC", "PhD"]

    def test_credential_titles_preserved_when_only_title_patched(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        """PATCH semantics: sending only ``title`` must not clear the
        previously-stored credential_titles."""
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                credential_titles=["PMHNP-BC"],
                credentials="PMHNP-BC",
            )
        )

        client.patch("/api/users/me", json={"title": "Dr."})

        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.title == "Dr."
        assert stored.credential_titles == ["PMHNP-BC"]

    def test_user_status_includes_credential_titles(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                credential_titles=["PMHNP-BC", "RN"],
                credentials="PMHNP-BC, RN",
            )
        )

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["credential_titles"] == ["PMHNP-BC", "RN"]


class TestOnboardingState:
    """Test the onboarding_state field on PATCH /api/users/me and
    GET /api/users/me/status."""

    def test_set_onboarding_state_completed(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"onboarding_state": "completed"})

        assert response.status_code == 200
        assert response.json()["onboarding_state"] == "completed"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.onboarding_state == "completed"

    def test_onboarding_state_accepts_all_three_values(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        for value in ("in_progress", "later", "completed"):
            response = client.patch("/api/users/me", json={"onboarding_state": value})
            assert response.status_code == 200
            assert response.json()["onboarding_state"] == value

    def test_invalid_onboarding_state_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"onboarding_state": "skipped"})
        assert response.status_code == 422

    def test_user_status_includes_onboarding_state(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes onboarding_state so a
        downstream deployment's overlay can decide whether to show the
        resume banner."""
        mock_user.onboarding_state = "later"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["onboarding_state"] == "later"

    def test_user_status_onboarding_state_null_for_grandfathered_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A row from before this column existed returns null, which
        a downstream deployment's overlay treats as 'already completed'
        (no banner, no redirect)."""
        mock_user.onboarding_state = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["onboarding_state"] is None

    def test_user_status_includes_baa_accepted_at(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes baa_accepted_at so a
        downstream deployment's onboarding wizard step registry can
        sequence the BAA step synchronously (no second API call)."""
        ts = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
        mock_user.baa_accepted_at = ts
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["baa_accepted_at"] is not None

    def test_user_status_baa_accepted_at_null_for_new_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A user who has never accepted the BAA returns null. The
        wizard treats this as the 'BAA step still required' signal."""
        mock_user.baa_accepted_at = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["baa_accepted_at"] is None


class TestSecurityGuideAcknowledgment:
    """Test POST /api/users/me/acknowledge-security-guide and the
    related GET endpoint + /me/status exposure."""

    def test_acknowledge_records_timestamp_and_version(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-05-14"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is True
        assert body["version"] == "2026-05-14"
        assert body["acknowledged_at"] is not None

        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.security_guide_version == "2026-05-14"
        assert stored.security_guide_acknowledged_at is not None

    def test_invalid_version_format_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """Versions must match YYYY-MM-DD, mirroring the BAA pattern."""
        mock_user_repo.update(mock_user)
        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "v2.0"},
        )
        assert response.status_code == 422

    def test_acknowledge_is_idempotent_and_overwrites(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """Re-acknowledging (e.g. after a version bump) overwrites
        both fields. The frontend prompts on version mismatch."""
        mock_user_repo.update(mock_user)
        client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-02-16"},
        )
        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-05-14"},
        )
        assert response.status_code == 200
        assert response.json()["version"] == "2026-05-14"

    def test_status_endpoint_reflects_acknowledgment(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.security_guide_acknowledged_at = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
        mock_user.security_guide_version = "2026-05-14"
        mock_user_repo.update(mock_user)

        response = client.get("/api/users/me/security-guide-status")

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is True
        assert body["version"] == "2026-05-14"

    def test_status_endpoint_reports_not_acknowledged(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.security_guide_acknowledged_at = None
        mock_user.security_guide_version = None
        mock_user_repo.update(mock_user)

        response = client.get("/api/users/me/security-guide-status")

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is False
        assert body["version"] is None
        assert body["acknowledged_at"] is None

    def test_user_status_includes_security_guide_fields(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes the guide fields so a
        downstream deployment's overlay can decide whether to redirect
        to the guide step."""
        mock_user.security_guide_acknowledged_at = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
        mock_user.security_guide_version = "2026-05-14"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_guide_version"] == "2026-05-14"
        assert body["security_guide_acknowledged_at"] is not None


class TestProfessionalInfo:
    """PATCH /api/users/me/professional-info splits professional
    credentials to their natural owners: ``legal_name`` to the user
    row, ``license_*`` to the clinician profile, ``business_address``
    to the practice row.
    """

    def test_legal_name_persists_on_user(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        with patch("app.auth.service._resolve_practice_from_email", return_value=None):
            response = client.patch(
                "/api/users/me/professional-info",
                json={"legal_name": "Jane Q. Therapist"},
            )
        assert response.status_code == 200
        assert response.json()["legal_name"] == "Jane Q. Therapist"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.legal_name == "Jane Q. Therapist"

    def test_license_persists_on_existing_profile(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                title="Dr.",
            )
        )
        response = client.patch(
            "/api/users/me/professional-info",
            json={"license_number": "PSY9001", "license_state": "NY"},
        )
        assert response.status_code == 200
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.license_number == "PSY9001"
        assert stored.license_state == "NY"
        # Unspecified field preserved — PATCH, not PUT.
        assert stored.title == "Dr."

    def test_dea_and_npi_persist_on_existing_profile(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-abc",
                license_number="PSY9001",
            )
        )
        response = client.patch(
            "/api/users/me/professional-info",
            json={"dea_number": "BT1234563", "npi_number": "1234567890"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["dea_number"] == "BT1234563"
        assert body["npi_number"] == "1234567890"
        stored = mock_clinician_profile_repo.get(mock_user.id)
        assert stored is not None
        assert stored.dea_number == "BT1234563"
        assert stored.npi_number == "1234567890"
        # Unspecified field preserved — PATCH, not PUT.
        assert stored.license_number == "PSY9001"

    def test_npi_must_be_ten_digits(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch(
            "/api/users/me/professional-info",
            json={"npi_number": "12345"},
        )
        assert response.status_code == 422

    def test_business_address_persists_on_practice(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        practice = SimpleNamespace(address=None)
        fake_session = MagicMock()
        fake_session.get.return_value = practice
        with (
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-1", "practice_1"),
            ),
            patch("app.db.get_db_session", return_value=fake_session),
        ):
            response = client.patch(
                "/api/users/me/professional-info",
                json={"business_address": "5 Oak Ave, Town, NY 10001"},
            )
        assert response.status_code == 200
        assert practice.address == "5 Oak Ave, Town, NY 10001"


class TestAcceptBaaSnapshot:
    """accept-baa snapshots the agreement onto the practice (the covered
    entity) and stamps the gate fields on the user row. Credentials are
    read from stored professional-info, not the request body.
    """

    def test_snapshot_written_to_practice_and_user_gate_stamped(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user.baa_accepted_at = None
        mock_user.legal_name = "Jane Q. Therapist"
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id,
                practice_id="practice-1",
                license_number="PSY9001",
                license_state="NY",
            )
        )
        practice = SimpleNamespace(
            name="Jane's Practice",
            address="5 Oak Ave",
            baa_accepted_at=None,
            baa_version=None,
            baa_legal_name=None,
            baa_license_number=None,
            baa_license_state=None,
            baa_practice_name=None,
            baa_business_address=None,
            baa_full_text=None,
        )
        fake_session = MagicMock()
        fake_session.get.return_value = practice
        with (
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-1", "practice_1"),
            ),
            patch("app.db.get_db_session", return_value=fake_session),
            patch("app.routes.users._resolve_baa_path") as mock_path,
        ):
            mock_path.return_value.read_text.return_value = "BAA TEXT BODY"
            response = client.post(
                "/api/users/me/accept-baa",
                json={"accepted": True, "version": "2024-01-01"},
            )
        assert response.status_code == 200
        # Snapshot landed on the practice (the covered entity).
        assert practice.baa_legal_name == "Jane Q. Therapist"
        assert practice.baa_license_number == "PSY9001"
        assert practice.baa_license_state == "NY"
        assert practice.baa_practice_name == "Jane's Practice"
        assert practice.baa_business_address == "5 Oak Ave"
        assert practice.baa_full_text == "BAA TEXT BODY"
        assert practice.baa_accepted_at is not None
        # Fast per-request gate stamped on the user row.
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.baa_accepted_at is not None
        assert stored.baa_version == "2024-01-01"


class TestBaaEndpointsNoMfaPosture:
    """Regression guard: BAA endpoints must be pre-MFA-onboarding (#2),
    not MFA-required (#1).

    The dashboard layout's redirect chain routes users through
    /baa-acceptance before they've necessarily completed MFA sign-in.
    If /me/baa-status or /me/accept-baa or /baa/{version} required MFA,
    the BAA page would fail to render and the user would be stranded
    (the 2026-05-19 launch-prep symptom).

    The ``client`` fixture installs a ``get_current_user_no_mfa``
    override that returns the mock user without any MFA assertion.
    These tests succeeding through that override is the contract: if
    a future PR adds a ``Depends(get_current_user)`` (transitively
    require_mfa) anywhere on the BAA chain, FastAPI will resolve the
    real dep instead of the override, the assertion below fires, and
    CI catches the regression at PR time.
    """

    def test_baa_status_resolves_via_no_mfa_override(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.get("/api/users/me/baa-status")
        assert response.status_code == 200, (
            f"expected 200 (no-MFA posture); got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert "accepted" in body
        assert "current_version" in body

    def test_accept_baa_resolves_via_no_mfa_override(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.baa_accepted_at = None
        mock_user_repo.update(mock_user)
        # Credentials are read from stored professional-info now, not the
        # request body — the body carries only version + accepted. Patch
        # the practice resolver to None so the practice-snapshot branch is
        # skipped (OSS single-tenant has no practice row); the user-row
        # gate stamp still runs.
        with patch("app.auth.service._resolve_practice_from_email", return_value=None):
            response = client.post(
                "/api/users/me/accept-baa",
                json={"accepted": True, "version": "2024-01-01"},
            )
        # Whether or not the version is bundled, the failure mode that
        # matters here is "403 MFA_REQUIRED" — anything else (200 success
        # or 404 unknown version) confirms the no-MFA posture.
        assert response.status_code != 403, (
            f"BAA accept must not require MFA; got 403: {response.text}"
        )

    def test_baa_text_resolves_via_no_mfa_override(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.get("/api/users/baa/2024-01-01")
        # Same posture check: 200 if version bundled, 404 if not, but
        # never 403 from an MFA requirement on the dep tree.
        assert response.status_code != 403, (
            f"BAA text must not require MFA; got 403: {response.text}"
        )

    def test_patch_me_resolves_via_no_mfa_override(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """Onboarding wizard PATCHes /api/users/me for provider_type and
        onboarding_state before the user has completed MFA. The endpoint
        must accept those updates without requiring the second-factor
        claim on the token."""
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"onboarding_state": "in_progress"})
        assert response.status_code == 200, (
            f"PATCH /me must not require MFA; got {response.status_code}: {response.text}"
        )
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.onboarding_state == "in_progress"


class TestRecordMfaEnrollment:
    """Test POST /api/users/me/mfa-enrolled.

    Covers two architectural concerns:

    1. THERAPY-glzf-2: the handler must look up the Firebase uid via the
       identity repository's reverse-lookup, not by assuming ``user.id``
       is the Firebase uid (it isn't, post-indirection).
    2. THERAPY-x08c: TOTP verification goes through the Identity Toolkit
       REST API, not the Python firebase_admin SDK (which doesn't expose
       MFA factors). Tests mock ``_user_has_totp_factor`` directly — the
       REST helper itself is exercised by its own tests below.
    """

    def test_resolves_firebase_uid_via_identity_repo(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_identity_repo: InMemoryIdentityRepository,
        mock_user_id: str,
    ) -> None:
        """Post-indirection: user.id is a Pablo uuid, Firebase uid is separate.

        The handler must pass the Firebase uid (from identity repo) to
        the TOTP verifier — not user.id. The default fixture pre-links
        (firebase, mock_user_id) -> mock_user_id as a legacy-backfill
        record; here we re-link to model a fresh signup where the two
        diverge.
        """
        mock_identity_repo._mappings.clear()
        mock_identity_repo.link("firebase", "firebase-uid-distinct", mock_user_id)
        mock_user_repo.update(mock_user)

        captured: dict[str, str] = {}

        def fake_check(uid: str) -> bool:
            captured["uid"] = uid
            return True

        with patch("app.routes.users._user_has_totp_factor", side_effect=fake_check):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 200
        assert "mfa_enrolled_at" in response.json()
        assert captured["uid"] == "firebase-uid-distinct", (
            "handler must use the identity-repo reverse-lookup, not user.id"
        )
        stored = mock_user_repo.get(mock_user_id)
        assert stored is not None
        assert stored.mfa_enrolled_at is not None

    def test_missing_identity_mapping_is_500(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_identity_repo: InMemoryIdentityRepository,
    ) -> None:
        """An authenticated user with no firebase identity row is a server
        invariant violation. Surface as 500 so the auth-failure alert
        fires instead of treating it as a routine client error."""
        mock_identity_repo._mappings.clear()
        mock_user_repo.update(mock_user)

        totp_check = MagicMock()
        with patch("app.routes.users._user_has_totp_factor", totp_check):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "IDENTITY_MAPPING_MISSING"
        totp_check.assert_not_called()
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.mfa_enrolled_at is None

    def test_no_totp_factor_rejected(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        mock_user_repo.update(mock_user)

        with patch("app.routes.users._user_has_totp_factor", return_value=False):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "MFA_NOT_ENROLLED"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.mfa_enrolled_at is None

    def test_identity_toolkit_user_miss_is_500(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        """Identity Toolkit returning no user for an authenticated principal
        is a server-side invariant violation, not a client error."""
        mock_user_repo.update(mock_user)

        with patch(
            "app.routes.users._user_has_totp_factor",
            side_effect=LookupError("no such user"),
        ):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "FIREBASE_USER_LOOKUP_MISS"

    def test_identity_toolkit_http_error_is_500(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        """Transport / non-2xx errors from Identity Toolkit don't 4xx the
        user — they're our problem, surface as 500 so they alert."""
        mock_user_repo.update(mock_user)

        with patch(
            "app.routes.users._user_has_totp_factor",
            side_effect=httpx.HTTPError("upstream blew up"),
        ):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "MFA_VERIFICATION_FAILED"


class TestUserHasTotpFactor:
    """Unit tests for the Identity Toolkit REST shim (THERAPY-x08c).

    These mock at the httpx + google.auth boundary so we exercise the
    request URL, body, and response parsing — but not the network.
    """

    @staticmethod
    def _mock_credentials() -> Any:
        creds = MagicMock()
        creds.token = "fake-bearer-token"
        return creds

    def _patch_auth_and_post(self, payload: dict[str, Any]) -> Any:
        """Returns a context manager that patches google.auth.default and
        httpx.post to return ``payload`` from accounts:lookup."""
        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.routes.users.google.auth.default",
                return_value=(self._mock_credentials(), "pablohealth-prod"),
            )
        )
        response_mock = MagicMock()
        response_mock.json.return_value = payload
        response_mock.raise_for_status.return_value = None
        post_mock = stack.enter_context(
            patch("app.routes.users.httpx.post", return_value=response_mock)
        )
        stack.post_mock = post_mock  # type: ignore[attr-defined]
        return stack

    def test_returns_true_when_totp_factor_present(self) -> None:
        payload = {
            "users": [
                {
                    "localId": "fb-uid-1",
                    "mfaInfo": [
                        {
                            "mfaEnrollmentId": "enrollment-1",
                            "totpInfo": {},
                        }
                    ],
                }
            ]
        }
        with self._patch_auth_and_post(payload) as stack:
            assert _user_has_totp_factor("fb-uid-1") is True
            # Confirm the request shape Identity Toolkit expects
            call = stack.post_mock.call_args
            assert call.args[0].endswith("/v1/accounts:lookup")
            assert call.kwargs["json"] == {"localId": ["fb-uid-1"]}
            assert call.kwargs["headers"]["Authorization"] == "Bearer fake-bearer-token"

    def test_returns_false_when_only_phone_factor(self) -> None:
        payload = {
            "users": [
                {
                    "localId": "fb-uid-2",
                    "mfaInfo": [
                        {
                            "mfaEnrollmentId": "enrollment-phone",
                            "phoneInfo": "+15555550100",
                        }
                    ],
                }
            ]
        }
        with self._patch_auth_and_post(payload):
            assert _user_has_totp_factor("fb-uid-2") is False

    def test_returns_false_when_no_factors(self) -> None:
        payload = {"users": [{"localId": "fb-uid-3"}]}
        with self._patch_auth_and_post(payload):
            assert _user_has_totp_factor("fb-uid-3") is False

    def test_raises_lookup_error_when_user_missing(self) -> None:
        """Identity Toolkit returning an empty users array for an
        authenticated principal is a server-side invariant violation."""
        payload: dict[str, Any] = {"users": []}
        with self._patch_auth_and_post(payload), pytest.raises(LookupError):
            _user_has_totp_factor("fb-uid-missing")
