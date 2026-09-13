# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a clinician sees when she asks where her panel applications stand.

The screen's whole argument is its order: what she owes comes first, soonest
deadline at the top, and everything Pablo is carrying follows. That ordering is
load-bearing rather than cosmetic — a board sorted by payer name makes her read
all of it to find the one thing she has to do, which is the process she was
trying to stop carrying. So it is asserted here rather than left to the
component.

``needs_you`` is asserted separately from the rows because a reminder built on
this endpoint and the screen rendering it must not be able to disagree about
whether she owes anything.

Runs the real router over an in-memory SQLite session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.auth.route_access import subscription_exempt
from app.auth.service import get_current_user, get_tenant_context
from app.db import get_db_session
from app.db.models import Base, PanelApplicationRow, PayerRow
from app.models import User
from app.routes import credentialing as credentialing_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.sqlite_engine import sqlite_engine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

_USER_ID = "11111111-1111-4111-8111-111111111111"
_OTHER_USER_ID = "22222222-2222-4222-8222-222222222222"
_URL = "/api/credentialing/panel-applications"

_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _user() -> User:
    return User(
        id=_USER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=_NOW,
        baa_accepted_at=_NOW,
        baa_version="2024-01-01",
    )


@pytest.fixture
def engine() -> Iterator[Engine]:
    tables = [Base.metadata.tables[n] for n in ("payers", "panel_applications")]
    with sqlite_engine(tables) as eng:
        yield eng


@pytest.fixture
def harness(engine: Engine) -> Iterator[dict[str, Any]]:
    session = Session(engine)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(credentialing_routes.router)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_tenant_context] = lambda: None
    app.dependency_overrides[subscription_exempt] = lambda: None
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {"client": client, "session": session}
    finally:
        session.close()


def _payer(session: Session, name: str) -> str:
    payer_id = str(uuid.uuid4())
    session.add(
        PayerRow(id=payer_id, name=name, payer_id="60054", created_at=_NOW, updated_at=_NOW)
    )
    session.flush()
    return payer_id


def _application(session: Session, payer_name: str, **overrides: Any) -> str:
    """One application against a payer of its own, so names stay distinguishable."""
    row_id = str(uuid.uuid4())
    fields: dict[str, Any] = {
        "id": row_id,
        "user_id": _USER_ID,
        "payer_id": _payer(session, payer_name),
        "status": "submitted",
        "action_owner": "pablo",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    fields.update(overrides)
    session.add(PanelApplicationRow(**fields))
    session.commit()
    return row_id


def _names(body: dict[str, Any]) -> list[str]:
    return [row["payer_name"] for row in body["data"]]


class TestWhenSheOwesNothing:
    def test_no_applications_at_all_is_an_answer_not_an_error(
        self, harness: dict[str, Any]
    ) -> None:
        body = harness["client"].get(_URL).json()

        assert body == {"data": [], "needs_you": 0}

    def test_applications_that_are_all_ours_report_nothing_owed(
        self, harness: dict[str, Any]
    ) -> None:
        """The case the screen has to say out loud rather than render as an empty table."""
        _application(harness["session"], "Aetna", action_owner="pablo")
        _application(harness["session"], "Cigna", action_owner="pablo")

        body = harness["client"].get(_URL).json()

        assert len(body["data"]) == 2
        assert body["needs_you"] == 0


class TestTheOrderIsTheArgument:
    def test_what_she_owes_comes_first_however_old_the_rest_is(
        self, harness: dict[str, Any]
    ) -> None:
        """Recency must not be able to bury the one row she has to act on."""
        _application(
            harness["session"],
            "Older, ours",
            action_owner="pablo",
            created_at=_NOW - timedelta(days=90),
        )
        _application(harness["session"], "Newer, hers", action_owner="therapist")

        assert _names(harness["client"].get(_URL).json()) == ["Newer, hers", "Older, ours"]

    def test_among_her_own_the_soonest_deadline_is_on_top(self, harness: dict[str, Any]) -> None:
        _application(
            harness["session"],
            "Due later",
            action_owner="therapist",
            due_at=_NOW + timedelta(days=30),
        )
        _application(
            harness["session"],
            "Due sooner",
            action_owner="therapist",
            due_at=_NOW + timedelta(days=3),
        )

        assert _names(harness["client"].get(_URL).json())[:2] == ["Due sooner", "Due later"]

    def test_an_undated_row_sorts_below_a_dated_one_rather_than_above_it(
        self, harness: dict[str, Any]
    ) -> None:
        """NULL due_at means no deadline WE set, not a deadline of zero.

        SQL sorts NULLs first by default on some backends, which would put
        every undated row above every dated one and bury the deadline that
        actually kills an application when it passes.
        """
        _application(harness["session"], "No deadline", action_owner="therapist", due_at=None)
        _application(
            harness["session"],
            "Has a deadline",
            action_owner="therapist",
            due_at=_NOW + timedelta(days=45),
        )

        assert _names(harness["client"].get(_URL).json()) == ["Has a deadline", "No deadline"]

    @pytest.mark.parametrize("status", ["effective", "denied"])
    def test_a_finished_application_sinks_even_when_it_is_marked_hers(
        self, harness: dict[str, Any], status: str
    ) -> None:
        """Contracted or refused, it is history — and history does not owe her anything."""
        _application(harness["session"], "Finished", action_owner="therapist", status=status)
        _application(harness["session"], "Still running", action_owner="pablo")

        assert _names(harness["client"].get(_URL).json()) == ["Still running", "Finished"]


class TestNeedsYou:
    def test_it_counts_only_what_is_hers(self, harness: dict[str, Any]) -> None:
        _application(harness["session"], "Hers", action_owner="therapist")
        _application(harness["session"], "Ours", action_owner="pablo")

        assert harness["client"].get(_URL).json()["needs_you"] == 1

    def test_a_finished_application_is_not_something_she_still_owes(
        self, harness: dict[str, Any]
    ) -> None:
        """An application that went effective while marked hers is done, not outstanding."""
        _application(harness["session"], "Contracted", action_owner="therapist", status="effective")

        body = harness["client"].get(_URL).json()

        assert len(body["data"]) == 1
        assert body["needs_you"] == 0


class TestWhatEachRowSays:
    def test_the_waiting_time_answers_the_question_she_actually_has(
        self, harness: dict[str, Any]
    ) -> None:
        """ "In review" does not say whether the silence means something is wrong."""
        _application(
            harness["session"],
            "Aetna",
            status="in_review",
            submitted_at=_NOW - timedelta(days=42),
        )

        row = harness["client"].get(_URL).json()["data"][0]

        assert row["status"] == "in_review"
        assert row["days_since_submitted"] >= 42

    def test_an_unsubmitted_application_has_no_waiting_time_rather_than_zero(
        self, harness: dict[str, Any]
    ) -> None:
        _application(harness["session"], "Aetna", status="researching", submitted_at=None)

        assert harness["client"].get(_URL).json()["data"][0]["days_since_submitted"] is None

    def test_what_it_is_waiting_on_is_passed_through_in_her_words(
        self, harness: dict[str, Any]
    ) -> None:
        """``awaiting`` is written for her, so it is shown rather than mapped to a label."""
        _application(
            harness["session"],
            "Cigna",
            action_owner="therapist",
            status="info_requested",
            awaiting="Sign and return the W-9 they sent on the 3rd.",
        )

        row = harness["client"].get(_URL).json()["data"][0]

        assert row["awaiting"] == "Sign and return the W-9 they sent on the 3rd."

    def test_the_payer_name_is_joined_rather_than_copied(self, harness: dict[str, Any]) -> None:
        """An insurer renamed in Settings is renamed here too, with no backfill."""
        session = harness["session"]
        _application(session, "Blue Cross")
        payer = session.query(PayerRow).one()
        payer.name = "BCBS of Arizona"
        session.commit()

        assert _names(harness["client"].get(_URL).json()) == ["BCBS of Arizona"]


class TestSheSeesOnlyHerOwn:
    def test_a_colleagues_application_does_not_come_back(self, harness: dict[str, Any]) -> None:
        """RLS refuses it in Postgres; this proves the query does not ask for it.

        The two are not redundant. SQLite enforces no policy, so a query that
        dropped the ``user_id`` filter would pass every other test in this file
        and fail only in production, where the policy is the thing standing
        between a clinician and which panels rejected her colleague.
        """
        _application(harness["session"], "Hers", user_id=_USER_ID)
        _application(harness["session"], "His", user_id=_OTHER_USER_ID)

        assert _names(harness["client"].get(_URL).json()) == ["Hers"]
