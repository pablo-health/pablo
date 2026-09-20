# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP-level tests for the clinician's read of a patient's intake forms.

``GET /api/patients/{patient_id}/intake-submissions`` is the other side of
the surface ``test_patient_intake_api.py`` covers. Three things are worth
proving separately from the submit path, and each has a test class here.

* **Which door it is.** The route is reached by a clinician who holds a
  grant, and not by the patient credential that wrote the row.
* **Which columns come out.** The stored payload also holds every PHQ-9 and
  GAD-7 answer. The response must not: those are outcome-measure rows, and
  the chart renders them from there.
* **What the audit says.** Opening a patient's own words is a disclosure, so
  it lands on the log — once per window, counting rather than quoting.

Two-clinician isolation against real policies is the integration suite's job
(``tests_integration/database/test_patient_intake_clinician_read_rls.py``).
Here the in-memory repository stands in for the grant, which is enough to
prove the route asks.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models import Patient
from app.models.audit import ACTOR_TYPE_CLINICIAN, AuditAction, ResourceType
from app.repositories import InMemoryPatientIntakeSubmissionRepository
from app.repositories.audit import InMemoryAuditRepository
from app.routes.patient_intake import (
    get_clinician_intake_submission_repository,
    get_clinician_patient_repository,
)
from app.services.audit_service import AuditService, get_audit_service
from app.settings import get_settings
from app.utcnow import utc_now
from fastapi import HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import InMemoryPatientRepository

_PATIENT = "11111111-1111-4111-8111-111111111111"
_OTHER_PATIENT = "22222222-2222-4222-8222-222222222222"
_UNKNOWN_PATIENT = "33333333-3333-4333-8333-333333333333"

# The reason text and the correction of the older submission. Kept as
# module constants so the log-hygiene test asserts against the same strings
# the route handled, not a paraphrase of them.
_REASON_NEW = "Panic at work for about two months."
_REASON_OLD = "Sleep has been bad since the spring."
_CORRECTION = "My last name is spelled Lovelace-Byron."

_PHQ9 = {str(i): 1 for i in range(1, 10)}
_GAD7 = {str(i): 1 for i in range(1, 8)}


def _url(patient_id: str) -> str:
    return f"/api/patients/{patient_id}/intake-submissions"


class _FakeRedis:
    """Enough of a Redis for the audit coalescing gate's ``SET NX EX``."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(
        self,
        key: str,
        value: str,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


def _stored(
    submission_id: str,
    patient_id: str,
    *,
    minutes_ago: int,
    reason_text: str,
    corrections: str | None = None,
    name_confirmed: bool = True,
    dob_confirmed: bool = True,
) -> dict[str, Any]:
    """A row shaped the way the submit route writes one."""
    submitted_at = utc_now() - timedelta(minutes=minutes_ago)
    return {
        "id": submission_id,
        "patient_id": patient_id,
        "submitted_at": submitted_at,
        "payload": {
            "form_version": 1,
            "name_confirmed": name_confirmed,
            "dob_confirmed": dob_confirmed,
            "corrections": corrections,
            "reason_text": reason_text,
            "instruments": {"phq9": dict(_PHQ9), "gad7": dict(_GAD7)},
            "outcome_measure_ids": {"phq9": "om-phq9", "gad7": "om-gad7"},
        },
        "created_by": patient_id,
        "created_at": submitted_at,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def submissions(mock_user_id: str) -> InMemoryPatientIntakeSubmissionRepository:
    """Two submissions for the patient under test, one for a stranger's.

    Deliberately inserted oldest-first, so "newest first" is a property of
    the read rather than of the insertion order.
    """
    repo = InMemoryPatientIntakeSubmissionRepository()
    repo.grant_access(_PATIENT, mock_user_id)
    repo.add_for_patient_principal(
        _stored(
            "intake-old",
            _PATIENT,
            minutes_ago=60,
            reason_text=_REASON_OLD,
            corrections=_CORRECTION,
            name_confirmed=False,
        )
    )
    repo.add_for_patient_principal(
        _stored("intake-new", _PATIENT, minutes_ago=5, reason_text=_REASON_NEW)
    )
    # A second patient's row, to prove the list is filtered rather than
    # merely ordered.
    repo.grant_access(_OTHER_PATIENT, mock_user_id)
    repo.add_for_patient_principal(
        _stored("intake-other", _OTHER_PATIENT, minutes_ago=1, reason_text="Not this one.")
    )
    return repo


@pytest.fixture
def audit_repo() -> InMemoryAuditRepository:
    """The real ``AuditService`` with its storage swapped.

    A hand-written double would imitate what the service records and then
    pass whether or not the service records it.
    """
    return InMemoryAuditRepository()


@pytest.fixture
def chart_client(
    client: TestClient,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
    submissions: InMemoryPatientIntakeSubmissionRepository,
    audit_repo: InMemoryAuditRepository,
) -> TestClient:
    """The shared authenticated client, pointed at this route's repositories."""
    now = utc_now()
    for patient_id, first, last in (
        (_PATIENT, "Ada", "Lovelace"),
        (_OTHER_PATIENT, "Grace", "Hopper"),
    ):
        mock_repo.create(
            Patient(
                id=patient_id,
                first_name=first,
                last_name=last,
                created_at=now,
                updated_at=now,
            ),
            mock_user_id,
        )

    app.dependency_overrides[get_clinician_patient_repository] = lambda: mock_repo
    app.dependency_overrides[get_clinician_intake_submission_repository] = lambda: submissions
    app.dependency_overrides[get_audit_service] = lambda: AuditService(audit_repo)
    return client


@pytest.fixture
def coalescing_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeRedis]:
    """Arm the audit read-coalescing window with a Redis that remembers."""
    fake = _FakeRedis()
    monkeypatch.setenv("AUDIT_READ_COALESCE_SECONDS", "900")
    monkeypatch.setattr("app.redis_client.get_redis_client", lambda: fake)
    get_settings.cache_clear()
    yield fake
    get_settings.cache_clear()


def _viewed(audit_repo: InMemoryAuditRepository, user_id: str) -> list[Any]:
    return [
        e
        for e in audit_repo.list_for_user(user_id)
        if e.action == AuditAction.PATIENT_INTAKE_SUBMISSION_VIEWED.value
    ]


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------


class TestAccess:
    def test_without_a_credential_is_401(self) -> None:
        """No overrides here: the real clinician door answers."""
        assert TestClient(app).get(_url(_PATIENT)).status_code == 401

    def test_a_patient_session_bearer_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The patient who wrote the row cannot read it back from here.

        A patient session credential is a bearer token like a clinician's
        and arrives in the same header, but it is not a clinician identity,
        so the identity verifier refuses it — which is stood in for here,
        because there is no Firebase in a unit test. What the assertion is
        worth is the next line down: the refusal ends the request. There is
        no patient-principal fallback on this route to pick the credential
        up afterwards.
        """

        def _not_a_clinician_identity(_token: str) -> dict[str, Any]:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "INVALID_TOKEN", "message": "", "details": {}}},
            )

        monkeypatch.setattr("app.auth.service.verify_token", _not_a_clinician_identity)

        response = TestClient(app).get(
            _url(_PATIENT),
            headers={"Authorization": "Bearer credential-of-the-patient"},
        )
        assert response.status_code == 401

    def test_an_unknown_patient_is_404(self, chart_client: TestClient) -> None:
        response = chart_client.get(_url(_UNKNOWN_PATIENT))
        assert response.status_code == 404

    def test_a_patient_with_no_submissions_is_an_empty_list(
        self,
        chart_client: TestClient,
        submissions: InMemoryPatientIntakeSubmissionRepository,
        mock_repo: InMemoryPatientRepository,
        mock_user_id: str,
    ) -> None:
        """200 and ``[]``, not 404. The chart exists; this surface is empty."""
        now = utc_now()
        empty_patient = "44444444-4444-4444-8444-444444444444"
        mock_repo.create(
            Patient(
                id=empty_patient,
                first_name="Alan",
                last_name="Turing",
                created_at=now,
                updated_at=now,
            ),
            mock_user_id,
        )
        submissions.grant_access(empty_patient, mock_user_id)

        response = chart_client.get(_url(empty_patient))
        assert response.status_code == 200
        assert response.json() == []

    def test_a_clinician_without_a_grant_sees_nothing(
        self,
        chart_client: TestClient,
        submissions: InMemoryPatientIntakeSubmissionRepository,
        mock_user_id: str,
    ) -> None:
        """Control first: the same request with the grant returns two rows.

        The rows are then moved to a repository holding no grant at all, so
        the empty answer below cannot come from an empty table.
        """
        with_grant = chart_client.get(_url(_PATIENT))
        assert [row["id"] for row in with_grant.json()] == ["intake-new", "intake-old"]

        ungranted = InMemoryPatientIntakeSubmissionRepository()
        for row in submissions.rows.values():
            ungranted.add_for_patient_principal(row)
        assert ungranted.rows, "the control's rows did not carry over"
        app.dependency_overrides[get_clinician_intake_submission_repository] = lambda: ungranted

        without_grant = chart_client.get(_url(_PATIENT))
        assert without_grant.status_code == 200
        assert without_grant.json() == []


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


class TestResponse:
    def test_newest_first(self, chart_client: TestClient) -> None:
        body = chart_client.get(_url(_PATIENT)).json()
        assert [row["id"] for row in body] == ["intake-new", "intake-old"]

    def test_only_another_patients_rows_are_left_out(self, chart_client: TestClient) -> None:
        body = chart_client.get(_url(_OTHER_PATIENT)).json()
        assert [row["id"] for row in body] == ["intake-other"]

    def test_exactly_the_pinned_fields(self, chart_client: TestClient) -> None:
        """The key set is asserted whole, so a new field cannot arrive unnoticed."""
        body = chart_client.get(_url(_PATIENT)).json()
        assert set(body[0]) == {
            "id",
            "submitted_at",
            "name_confirmed",
            "dob_confirmed",
            "corrections",
            "reason_text",
        }

    def test_no_item_scores_anywhere_in_the_response(self, chart_client: TestClient) -> None:
        """The stored payload holds every answer. The response holds none.

        Asserted against the serialized body rather than the parsed keys,
        so a score nested inside some future field would still fail.
        """
        raw = chart_client.get(_url(_PATIENT)).text
        for absent in ("phq9", "gad7", "instruments", "outcome_measure_ids", "form_version"):
            assert absent not in raw

    def test_the_reason_text_and_attestation_come_through(self, chart_client: TestClient) -> None:
        newest, oldest = chart_client.get(_url(_PATIENT)).json()

        assert newest["reason_text"] == _REASON_NEW
        assert newest["name_confirmed"] is True
        assert newest["dob_confirmed"] is True
        assert newest["corrections"] is None

        assert oldest["reason_text"] == _REASON_OLD
        assert oldest["name_confirmed"] is False
        assert oldest["corrections"] == _CORRECTION

    def test_a_row_from_an_older_form_reads_as_nothing_flagged(
        self,
        chart_client: TestClient,
        submissions: InMemoryPatientIntakeSubmissionRepository,
    ) -> None:
        """A payload predating the attestation questions is not an error.

        The column records what some version of the form sent. A row
        without the confirm flags reads as confirmed rather than as a
        correction nobody made.
        """
        submissions.rows.clear()
        submissions.add_for_patient_principal(
            {
                "id": "intake-ancient",
                "patient_id": _PATIENT,
                "submitted_at": utc_now(),
                "payload": {"reason_for_visit": "an older shape"},
                "created_by": _PATIENT,
                "created_at": utc_now(),
            }
        )

        (row,) = chart_client.get(_url(_PATIENT)).json()
        assert row["name_confirmed"] is True
        assert row["dob_confirmed"] is True
        assert row["corrections"] is None
        assert row["reason_text"] == ""


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


class TestAudit:
    def test_the_read_is_recorded_against_the_patient(
        self,
        chart_client: TestClient,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        chart_client.get(_url(_PATIENT))

        (entry,) = _viewed(audit_repo, mock_user_id)
        assert entry.actor_type == ACTOR_TYPE_CLINICIAN
        assert entry.resource_type == ResourceType.PATIENT_INTAKE_SUBMISSION.value
        assert entry.resource_id == _PATIENT
        assert entry.patient_id == _PATIENT
        assert entry.changes == {"count": 2}

    def test_an_empty_read_is_still_recorded(
        self,
        chart_client: TestClient,
        audit_repo: InMemoryAuditRepository,
        submissions: InMemoryPatientIntakeSubmissionRepository,
        mock_user_id: str,
    ) -> None:
        """Nothing came back, but the chart was opened — that is the fact."""
        submissions.rows.clear()
        chart_client.get(_url(_PATIENT))

        (entry,) = _viewed(audit_repo, mock_user_id)
        assert entry.changes == {"count": 0}

    def test_a_refused_read_records_nothing(
        self, chart_client: TestClient, audit_repo: InMemoryAuditRepository, mock_user_id: str
    ) -> None:
        """The 404 happens before the audit write, not after."""
        chart_client.get(_url(_UNKNOWN_PATIENT))
        assert _viewed(audit_repo, mock_user_id) == []

    @pytest.mark.usefixtures("coalescing_on")
    def test_a_second_read_in_the_window_writes_no_second_row(
        self,
        chart_client: TestClient,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        """The card refetches with the chart; the log should not grow with it."""
        for _ in range(3):
            chart_client.get(_url(_PATIENT))

        assert len(_viewed(audit_repo, mock_user_id)) == 1

    @pytest.mark.usefixtures("coalescing_on")
    def test_a_different_patient_in_the_same_window_is_its_own_row(
        self,
        chart_client: TestClient,
        audit_repo: InMemoryAuditRepository,
        mock_user_id: str,
    ) -> None:
        """Control for the test above: coalescing is per patient, not global."""
        chart_client.get(_url(_PATIENT))
        chart_client.get(_url(_OTHER_PATIENT))

        assert {e.resource_id for e in _viewed(audit_repo, mock_user_id)} == {
            _PATIENT,
            _OTHER_PATIENT,
        }

    def test_nothing_the_patient_wrote_reaches_the_logs(
        self, chart_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("DEBUG"):
            chart_client.get(_url(_PATIENT))

        logged = caplog.text
        for secret in (_REASON_NEW, _REASON_OLD, _CORRECTION, "Lovelace", "Ada"):
            assert secret not in logged
