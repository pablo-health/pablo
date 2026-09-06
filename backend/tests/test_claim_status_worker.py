# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The poll backstop (``app.claims.status_worker``) and the on-demand check."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from app.claims.status_worker import check_status, poll_acknowledgments

from tests.claims_pipeline_fakes import NOW, PipelineHarness, make_harness, restore_listeners

if TYPE_CHECKING:
    from collections.abc import Iterator

_TWO_HOURS_AGO = NOW - timedelta(hours=2)


@pytest.fixture
def harness() -> Iterator[PipelineHarness]:
    built = make_harness()
    yield built
    restore_listeners()


def _poll(harness: PipelineHarness) -> object:
    return poll_acknowledgments(
        harness.pipeline, harness.client, practice_user_ids=harness.practice_users()
    )


def test_a_claim_waiting_over_an_hour_is_asked_about_and_moved(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="submitted", submitted_at=_TWO_HOURS_AGO)
    harness.client.acknowledge("clearinghouse_forwarded", created.control_number)

    summary = _poll(harness)

    saved = harness.get(created.id)
    assert saved.state == "ch_accepted"
    assert saved.status_checked_at == NOW
    assert summary.moved == 1  # type: ignore[attr-defined]
    assert harness.client.feed_reads == 1
    assert harness.client.report_reads == 1


def test_a_claim_heard_from_recently_is_not_asked_about(harness: PipelineHarness) -> None:
    harness.add(state="submitted", submitted_at=NOW - timedelta(minutes=10))

    summary = _poll(harness)

    assert summary.checked == 0  # type: ignore[attr-defined]
    assert harness.client.feed_reads == 0


def test_the_feed_is_read_once_for_every_waiting_claim(harness: PipelineHarness) -> None:
    first = harness.add(state="submitted", submitted_at=_TWO_HOURS_AGO)
    second = harness.add(state="ch_accepted", submitted_at=_TWO_HOURS_AGO)
    harness.client.acknowledge("payer_accepted", first.control_number)
    harness.client.acknowledge("payer_rejected", second.control_number)

    _poll(harness)

    assert harness.client.feed_reads == 1
    assert harness.get(first.id).state == "payer_accepted"
    assert harness.get(second.id).state == "rejected"


def test_a_277_already_applied_is_not_read_again(harness: PipelineHarness) -> None:
    created = harness.add(state="submitted", submitted_at=_TWO_HOURS_AGO)
    harness.client.acknowledge("clearinghouse_forwarded", created.control_number)
    _poll(harness)
    harness.pipeline.now = lambda: NOW + timedelta(hours=2)

    _poll(harness)

    assert harness.client.report_reads == 1
    assert len(harness.receipts.list_for_claim(created.id)) == 1


def test_a_277_naming_somebody_elses_claim_is_not_fetched(harness: PipelineHarness) -> None:
    harness.add(state="submitted", submitted_at=_TWO_HOURS_AGO)
    harness.client.acknowledge("payer_accepted", "NOTWAITING")

    _poll(harness)

    assert harness.client.report_reads == 0


def test_check_status_records_that_somebody_looked_when_nothing_is_new(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="submitted", submitted_at=_TWO_HOURS_AGO)

    checked = check_status(harness.pipeline, harness.client, created)

    assert checked.state == "submitted"
    assert checked.status_checked_at == NOW
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "status_checked"
    assert harness.client.feed_reads == 1


def test_check_status_applies_what_the_feed_says(harness: PipelineHarness) -> None:
    created = harness.add(state="submitted", submitted_at=NOW - timedelta(minutes=5))
    harness.client.acknowledge("payer_accepted", created.control_number)

    checked = check_status(harness.pipeline, harness.client, created)

    assert checked.state == "payer_accepted"
    assert [r.kind for r in harness.receipts.list_for_claim(created.id)] == ["payer_accepted"]


def test_check_status_on_a_finished_claim_does_not_read_the_feed(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="paid", submitted_at=_TWO_HOURS_AGO, adjudicated_at=NOW)

    check_status(harness.pipeline, harness.client, created)

    assert harness.client.feed_reads == 0
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "status_checked"
