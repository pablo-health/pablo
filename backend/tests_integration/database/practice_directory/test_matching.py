# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Matching behavior."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

from .helpers import (
    _A,
    _CALENDAR,
    _FAMILY,
    _MAIN,
)
from .matching import (
    _calendar_service,
    _live_charts,
    _match,
    _proposal,
    _repos,
    _series_key,
    _tenant_session,
    _users,
)

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)


class TestStrongAndWeakEvidence:
    def test_their_client_by_name_and_birthday_is_a_certain_hidden_match(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import PatientHint

        result = _match(
            engine,
            practice["schema"],
            PatientHint(full_name="Grace Hopper", date_of_birth=date(1980, 1, 2)),
        )

        assert (result.patient_id, result.evidence) == (practice["theirs"], "name_and_dob")
        assert not result.visible

    def test_their_client_by_initials_alone_is_no_match(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import PatientHint

        result = _match(engine, practice["schema"], PatientHint(initials="G.H."))

        assert result.patient_id is None
        assert result.possible_ids == []

    def test_their_client_by_name_alone_is_no_match(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import PatientHint

        result = _match(engine, practice["schema"], PatientHint(full_name="Grace Hopper"))

        assert result.patient_id is None
        assert result.possible_ids == []


@pytest.mark.usefixtures("remembered_as_theirs")
class TestMatchingAColleaguesClient:
    def test_an_import_of_their_client_says_who_sees_them_and_creates_nothing(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import MatchContext
        from app.patients.seen_by import SeenBy
        from app.routes.calendar_import import _to_response

        before = _live_charts(engine, practice["schema"])
        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings, main_calendar_id=_MAIN)
            response = _to_response(_proposal("Grace Hopper"), ctx, SeenBy(_users()), _CALENDAR)
        finally:
            session.close()
            conn.close()

        [series] = response.series
        assert series.match.seen_by == ["Dr. Hopper"]
        assert series.match.patient is None
        assert series.match.possible == []
        assert series.preselected is False
        assert _live_charts(engine, practice["schema"]) == before

    def test_a_confirm_naming_their_chart_is_refused(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.api_errors import BadRequestError
        from app.auth.service import TenantContext
        from app.models.scheduling import (
            ConfirmImportRequest,
            ConfirmImportSeries,
        )
        from app.routes.calendar_import import confirm_calendar_import

        before = _live_charts(engine, practice["schema"])
        scheduling = MagicMock()
        start = datetime.now(UTC) + timedelta(days=3)
        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            for patient_id in (practice["theirs"], None):
                with pytest.raises(BadRequestError):
                    confirm_calendar_import(
                        http_request=MagicMock(),
                        request=ConfirmImportRequest(
                            series=[
                                ConfirmImportSeries(
                                    candidate_key="k1",
                                    display_name="Grace Hopper",
                                    patient_id=patient_id,
                                    source_identifier=_series_key(),
                                    start_at=start,
                                    duration_minutes=50,
                                    cadence="weekly",
                                    occurrences=2,
                                )
                            ]
                        ),
                        ctx=TenantContext(user_id=_A),
                        user=MagicMock(),
                        patient_repo=patients,
                        mappings=mappings,
                        scheduling=scheduling,
                        user_repo=_users(),
                        audit=MagicMock(),
                        owner_tz=UTC,
                        service=_calendar_service(),
                    )
        finally:
            session.close()
            conn.close()

        scheduling.create_recurring.assert_not_called()
        assert _live_charts(engine, practice["schema"]) == before

    def test_my_own_client_still_matches(self, engine: Engine, practice: dict[str, str]) -> None:
        from app.patients.matching import MatchContext, PatientHint, match_patient

        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings)
            result = match_patient(PatientHint(full_name="Ada Lovelace"), ctx)
        finally:
            session.close()
            conn.close()

        assert result.patient_id == practice["mine"]
        assert result.visible

    def test_an_email_two_charts_share_is_possible_not_certain(
        self, engine: Engine, practice: dict[str, str]
    ) -> None:
        from app.patients.matching import MatchContext, PatientHint, match_patient

        conn, session = _tenant_session(engine, practice["schema"], _A)
        try:
            patients, mappings = _repos(session)
            ctx = MatchContext.for_practice(_A, patients, mappings)
            result = match_patient(PatientHint(email=_FAMILY), ctx)
        finally:
            session.close()
            conn.close()

        assert result.patient_id is None
        assert sorted(result.possible_ids) == sorted(
            [practice["shared_mine"], practice["shared_theirs"]]
        )
        assert result.hidden_ids == [practice["shared_theirs"]]
