# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Re-emitting open holds, and saying so when there are none.

The heartbeat assertions are about counting: exactly one line per run, never
zero, never two. The signal a reader acts on is its ABSENCE, so duplicates
teach them to tolerate noise and a silent quiet run is indistinguishable
from a dead tick.

The PHI test runs against a FULLY POPULATED hold — one populating nothing
would pass on an emitter that leaked every field it was given.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from app.claims.fanout import PracticeContext
from app.claims.hold_emission import (
    HEARTBEAT_EVENT,
    HOLD_EVENT,
    emit_heartbeat,
    emit_open,
    fields_for,
)
from app.jobs import claims_pipeline as job
from app.models.claims_holds import RemittanceHold
from app.repositories.remittance_hold import InMemoryRemittanceHoldRepository

_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_NOW = _AT + timedelta(hours=30)


def _hold(**overrides) -> RemittanceHold:
    fields = {
        "id": "hold-1",
        "claim_id": "claim-1",
        "patient_id": "patient-1",
        "control_number": "CLM0001",
        "posting_key": "claim-1:835:txn:CLM0001",
        "reason": "patient_responsibility",
        "stated_cents": 3_000,
        "computed_cents": 0,
        "patient_responsibility_cents": 3_000,
        "codes": [{"group_code": "CO", "reason_code": "45"}],
        "line_count": 1,
        "payer_name": "Aetna",
        "detected_at": _AT,
    }
    return RemittanceHold(**{**fields, **overrides})


def _events(caplog: pytest.LogCaptureFixture, event_type: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event_type", None) == event_type]


class TestEveryOpenHoldIsReEmitted:
    def test_one_event_per_open_hold(self, caplog: pytest.LogCaptureFixture) -> None:
        repo = InMemoryRemittanceHoldRepository()
        repo.add(_hold(id="h1", posting_key="k1"))
        repo.add(_hold(id="h2", posting_key="k2", claim_id="claim-2"))

        with caplog.at_level(logging.INFO):
            assert emit_open(repo, now=_NOW) == 2

        assert {r.hold_id for r in _events(caplog, HOLD_EVENT)} == {"h1", "h2"}

    def test_the_same_hold_is_emitted_again_on_the_next_tick(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A dropped line must cost one tick, not the hold."""
        repo = InMemoryRemittanceHoldRepository()
        repo.add(_hold())

        with caplog.at_level(logging.INFO):
            emit_open(repo, now=_NOW)
            emit_open(repo, now=_NOW + timedelta(hours=1))

        assert len(_events(caplog, HOLD_EVENT)) == 2

    def test_an_acknowledged_hold_is_still_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """Somebody saying they have seen it is not somebody deciding, and
        the client is still unbilled."""
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_hold())
        repo.acknowledge(hold.id, at=_AT)

        with caplog.at_level(logging.INFO):
            assert emit_open(repo, now=_NOW) == 1

        [event] = _events(caplog, HOLD_EVENT)
        assert event.state == "acknowledged"

    def test_a_resolved_hold_stops_on_the_next_tick(self, caplog: pytest.LogCaptureFixture) -> None:
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_hold())
        repo.resolve(hold.id, finding="waived", user_id="u1", at=_AT)

        with caplog.at_level(logging.INFO):
            assert emit_open(repo, now=_NOW) == 0

        assert _events(caplog, HOLD_EVENT) == []

    def test_the_event_carries_the_age_a_reader_triages_on(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        repo = InMemoryRemittanceHoldRepository()
        repo.add(_hold())

        with caplog.at_level(logging.INFO):
            emit_open(repo, now=_NOW)

        [event] = _events(caplog, HOLD_EVENT)
        assert event.age_hours == 30

    def test_nothing_is_emitted_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A hold is an expected business event.

        At ERROR it moves error-rate SLOs and teaches whoever reads them
        that "incident" sometimes means "a customer did an expected thing".
        """
        repo = InMemoryRemittanceHoldRepository()
        repo.add(_hold())

        with caplog.at_level(logging.DEBUG):
            emit_open(repo, now=_NOW)
            emit_heartbeat(1)

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_a_deployment_with_no_hold_repository_reports_zero(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO):
            assert emit_open(None, now=_NOW) == 0

        assert _events(caplog, HOLD_EVENT) == []


class TestTheHeartbeatCounts:
    def test_a_quiet_tick_still_says_so(self, caplog: pytest.LogCaptureFixture) -> None:
        """The load-bearing case. Silence must mean "broken", not "quiet"."""
        with caplog.at_level(logging.INFO):
            emit_heartbeat(0)

        [beat] = _events(caplog, HEARTBEAT_EVENT)
        assert beat.open == 0

    def test_a_busy_tick_says_how_many(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            emit_heartbeat(3)

        [beat] = _events(caplog, HEARTBEAT_EVENT)
        assert beat.open == 3

    def test_one_call_emits_exactly_one_line(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO):
            emit_heartbeat(0)

        assert len(_events(caplog, HEARTBEAT_EVENT)) == 1


class TestNoPatientReachesTheLog:
    """Asserted against a fully populated hold, not an empty one."""

    def test_no_client_identifier_crosses(self) -> None:
        hold = _hold(patient_id="the-client-uuid")

        fields = fields_for(hold, now=_NOW)

        assert "patient_id" not in fields
        assert "the-client-uuid" not in str(fields)

    def test_the_fields_that_do_cross_are_the_ones_a_reader_triages_on(self) -> None:
        fields = fields_for(_hold(), now=_NOW)

        assert fields["event_type"] == HOLD_EVENT
        assert fields["hold_id"] == "hold-1"
        assert fields["state"] == "open"
        assert fields["reason"] == "patient_responsibility"
        assert fields["age_hours"] == 30
        assert fields["payer_name"] == "Aetna"
        assert fields["codes"] == ["CO-45"]

    def test_no_date_of_service_and_no_name(self) -> None:
        """A claim has exactly one client, so a date of service would pin a
        person to everything else in the line."""
        fields = fields_for(_hold(), now=_NOW)

        assert not any("date" in key or "dob" in key or key == "name" for key in fields)
        # The payer's name is not a person's.
        assert (
            set(fields)
            - {
                "event_type",
                "hold_id",
                "claim_id",
                "control_number",
                "state",
                "reason",
                "age_hours",
                "stated_cents",
                "computed_cents",
                "delta_cents",
                "line_count",
                "payer_name",
                "codes",
            }
            == set()
        )


class TestTheScheduledRunEmitsOnce:
    """The wiring, not the emitter.

    ``emit_heartbeat`` being correct is worth nothing if the tick never
    calls it, which is the same failure this whole series started from.
    """

    def _run(self, monkeypatch, *, practices: int, holds: int) -> None:
        contexts = [
            PracticeContext(schema=f"p{i}", practice_id=f"p{i}", client=object(), user_ids=["u1"])
            for i in range(practices)
        ]
        monkeypatch.setattr(
            job, "active_practices", lambda *, max_tenants: iter(contexts[:max_tenants])
        )
        monkeypatch.setattr(
            job,
            "run_practice",
            lambda _practice, _stages, *, max_per_tenant: Counter(
                {"holds_open": holds, "limit": max_per_tenant}
            ),
        )
        job.run_pipeline(["remit"])

    def test_a_run_with_no_practices_still_emits_exactly_one_heartbeat(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The case that catches a dead deployment.

        No practices means no holds, which is exactly when a mechanism that
        only spoke up on bad news would go silent and stay silent.
        """
        with caplog.at_level(logging.INFO):
            self._run(monkeypatch, practices=0, holds=0)

        [beat] = _events(caplog, HEARTBEAT_EVENT)
        assert beat.open == 0

    def test_many_practices_still_emit_exactly_one_heartbeat(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Per run, not per practice. A reader watching for one line a tick
        must not have to know how many practices exist."""
        with caplog.at_level(logging.INFO):
            self._run(monkeypatch, practices=3, holds=2)

        [beat] = _events(caplog, HEARTBEAT_EVENT)
        assert beat.open == 6

    def test_a_run_that_dies_halfway_still_reports_what_it_found(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A run that says nothing must mean the tick is gone, not that it
        went badly — otherwise the absence signal fires on the wrong thing."""
        context = PracticeContext(schema="p0", practice_id="p0", client=object(), user_ids=["u1"])
        monkeypatch.setattr(
            job, "active_practices", lambda *, max_tenants: iter([context][:max_tenants])
        )

        def boom(*_args, **_kwargs):
            raise RuntimeError("the database went away")

        monkeypatch.setattr(job, "run_practice", boom)

        with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
            job.run_pipeline(["remit"])

        assert len(_events(caplog, HEARTBEAT_EVENT)) == 1
