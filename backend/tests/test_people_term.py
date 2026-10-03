# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Whether the app says "clients" or "patients", and the routes that set it."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.models import User
from app.people_term import people_words, resolve_people_term, suggest_people_term
from app.repositories import (
    ClinicianProfile,
    InMemoryClinicianProfileRepository,
    InMemoryUserRepository,
)


class TestSuggest:
    @pytest.mark.parametrize(
        "titles",
        [["PMHNP-BC"], ["MD"], ["DO"], ["PA-C"], ["NP"], ["APRN", "LMFT"], ["M.D."]],
    )
    def test_a_prescriber_license_suggests_patients(self, titles: list[str]) -> None:
        assert suggest_people_term(provider_type=None, credential_titles=titles) == "patients"

    @pytest.mark.parametrize("titles", [["LCSW"], ["LPC"], ["LMFT"], ["Psy.D."], ["PhD", "LP"]])
    def test_a_therapy_license_suggests_clients(self, titles: list[str]) -> None:
        assert suggest_people_term(provider_type=None, credential_titles=titles) == "clients"

    @pytest.mark.parametrize("provider_type", ["prescriber", "both"])
    def test_a_prescribing_clinician_type_suggests_patients(self, provider_type: str) -> None:
        assert suggest_people_term(provider_type=provider_type) == "patients"

    def test_prescriber_details_suggest_patients(self) -> None:
        assert suggest_people_term(provider_type="therapist", dea_number="AB1234563") == "patients"

    def test_a_prescriber_license_outranks_the_therapist_type(self) -> None:
        assert suggest_people_term(provider_type="therapist", credentials="MD") == "patients"

    def test_a_therapist_type_alone_suggests_clients(self) -> None:
        assert suggest_people_term(provider_type="therapist") == "clients"

    def test_nothing_known_settles_nothing(self) -> None:
        assert suggest_people_term(provider_type=None, credential_titles=["RN"]) is None


class TestResolve:
    def test_own_choice_wins(self) -> None:
        assert (
            resolve_people_term(choice="clients", suggested="patients", practice_default="patients")
            == "clients"
        )

    def test_license_comes_before_the_practice_default(self) -> None:
        assert (
            resolve_people_term(choice=None, suggested="clients", practice_default="patients")
            == "clients"
        )

    def test_practice_default_when_the_license_does_not_settle_it(self) -> None:
        assert (
            resolve_people_term(choice=None, suggested=None, practice_default="patients")
            == "patients"
        )

    def test_clients_when_nothing_is_known(self) -> None:
        assert resolve_people_term(choice=None, suggested=None, practice_default=None) == "clients"


def test_people_words_forms() -> None:
    words = people_words("patients")
    assert (words.one, words.many, words.One, words.Many) == (
        "patient",
        "patients",
        "Patient",
        "Patients",
    )


def _practice(**overrides: Any) -> SimpleNamespace:
    values = {"id": "practice-1", "owner_email": "test@example.com", "people_term": None}
    values.update(overrides)
    return SimpleNamespace(**values)


class TestRoutes:
    def _call(self, client: Any, method: str, path: str, practice: Any, **kwargs: Any) -> Any:
        session = MagicMock()
        session.get.return_value = practice
        with (
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-1", "practice_1"),
            ),
            patch("app.db.get_db_session", return_value=session),
        ):
            return getattr(client, method)(path, **kwargs)

    def test_a_new_practice_defaults_to_clients(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.provider_type = None
        mock_user_repo.update(mock_user)
        body = self._call(client, "get", "/api/users/me/people-term", _practice()).json()
        assert body["people_term"] == "clients"
        assert body["choice"] is None
        assert body["practice_default"] is None
        assert body["can_set_practice_default"] is True

    def test_a_prescriber_license_reads_patients(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_clinician_profile_repo: InMemoryClinicianProfileRepository,
    ) -> None:
        mock_user_repo.update(mock_user)
        mock_clinician_profile_repo.create(
            ClinicianProfile(
                user_id=mock_user.id, practice_id="practice-1", credential_titles=["PMHNP-BC"]
            )
        )
        body = self._call(client, "get", "/api/users/me/people-term", _practice()).json()
        assert body["suggested"] == "patients"
        assert body["people_term"] == "patients"

    def test_own_choice_round_trips_and_clears(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        set_body = self._call(
            client,
            "put",
            "/api/users/me/people-term",
            _practice(),
            json={"people_term": "patients"},
        ).json()
        assert set_body["choice"] == "patients"
        assert set_body["people_term"] == "patients"
        assert mock_user_repo.get_preferences(mock_user.id).people_term == "patients"

        cleared = self._call(
            client, "put", "/api/users/me/people-term", _practice(), json={"people_term": None}
        ).json()
        assert cleared["choice"] is None

    def test_an_unknown_word_is_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = self._call(
            client, "put", "/api/users/me/people-term", _practice(), json={"people_term": "folks"}
        )
        assert response.status_code == 422

    def test_owner_sets_the_practice_default(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.provider_type = None
        mock_user_repo.update(mock_user)
        practice = _practice()
        body = self._call(
            client,
            "put",
            "/api/users/me/practice/people-term",
            practice,
            json={"people_term": "patients"},
        ).json()
        assert practice.people_term == "patients"
        assert body["practice_default"] == "patients"
        assert body["people_term"] == "patients"

    def test_non_owner_cannot_set_the_practice_default(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        practice = _practice(owner_email="someone-else@example.com")
        response = self._call(
            client,
            "put",
            "/api/users/me/practice/people-term",
            practice,
            json={"people_term": "patients"},
        )
        assert response.status_code == 403
        assert practice.people_term is None
        get_body = self._call(client, "get", "/api/users/me/people-term", practice).json()
        assert get_body["can_set_practice_default"] is False
