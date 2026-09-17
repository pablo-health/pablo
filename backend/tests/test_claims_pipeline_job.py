# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The scheduled pipeline (``app.jobs.claims_pipeline``): the fan-out and its bounds.

Each practice is visited once, each of its clinicians in their own unit of
work, the stages in order; ``--max-tenants`` bounds the run; a clinician
whose unit of work fails does not stop the next one, and neither does a
whole practice that fails or cannot be resolved at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.claims import fanout
from app.claims.fanout import PracticeContext, TenantRun
from app.jobs import claims_pipeline as job

from tests.claims_fixtures import USER_ID
from tests.claims_pipeline_fakes import (
    ACCOUNT,
    NOW,
    PipelineHarness,
    make_harness,
    restore_listeners,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def practices(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    harnesses = {
        "practice_a": make_harness(now=NOW, principal=USER_ID),
        "practice_b": make_harness(now=NOW, principal=USER_ID),
    }
    contexts = [
        PracticeContext(
            schema=schema, practice_id=schema, client=harness.client, user_ids=[USER_ID, "u2"]
        )
        for schema, harness in harnesses.items()
    ]
    visited: list[tuple[str, str]] = []
    failing: set[tuple[str, str]] = set()

    def for_each_clinician(
        practice: PracticeContext, work: Callable[[TenantRun, str], None]
    ) -> int:
        completed = 0
        harness = harnesses[practice.schema]
        for user_id in practice.user_ids:
            visited.append((practice.schema, user_id))
            if (practice.schema, user_id) in failing:
                continue
            harness.pipeline.principal_user_id = user_id
            work(
                TenantRun(pipeline=harness.pipeline, payers=harness.payers, commit=harness.commit),
                user_id,
            )
            completed += 1
        return completed

    monkeypatch.setattr(
        job, "active_practices", lambda *, max_tenants: iter(contexts[:max_tenants])
    )
    monkeypatch.setattr(job, "for_each_clinician", for_each_clinician)
    monkeypatch.setattr(job, "_account_for", lambda _practice: ACCOUNT)
    yield {"harnesses": harnesses, "visited": visited, "failing": failing}
    restore_listeners()


def test_every_practice_and_clinician_is_visited_in_order(practices: dict[str, Any]) -> None:
    a: PipelineHarness = practices["harnesses"]["practice_a"]
    b: PipelineHarness = practices["harnesses"]["practice_b"]
    a_claim = a.add(state="validated")
    b_claim = b.add(state="validated")

    totals = job.run_pipeline()

    assert practices["visited"] == [
        ("practice_a", USER_ID),
        ("practice_a", "u2"),
        ("practice_b", USER_ID),
        ("practice_b", "u2"),
    ]
    assert totals["practices"] == 2
    assert totals["clinicians"] == 4
    assert totals["submit_submitted"] == 2
    assert a.get(a_claim.id).state == "submitted"
    assert b.get(b_claim.id).state == "submitted"


def test_the_run_is_bounded_by_max_tenants(practices: dict[str, Any]) -> None:
    totals = job.run_pipeline(max_tenants=1)

    assert totals["practices"] == 1
    assert {schema for schema, _ in practices["visited"]} == {"practice_a"}


def test_a_single_stage_can_be_run(practices: dict[str, Any]) -> None:
    a: PipelineHarness = practices["harnesses"]["practice_a"]
    a.add(state="validated")

    totals = job.run_pipeline(("watchdog",))

    assert "submit_submitted" not in totals
    assert totals["watchdog_checked"] >= 1
    assert a.client.submissions == []


def test_the_cli_maps_its_flags(practices: dict[str, Any]) -> None:
    assert job.run(["--stage", "status", "--max-tenants", "1", "--max-per-tenant", "3"]) == 0
    assert {schema for schema, _ in practices["visited"]} == {"practice_a"}


def test_one_failing_practice_does_not_stop_the_rest(
    practices: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A practice whose preamble raises must not abandon the practices after it.

    The per-practice preamble — the billing profile read, the remittance
    timelines — runs outside ``for_each_clinician``'s per-clinician guard. So
    a schema missing a table raised straight out of the loop body and the
    fan-out stopped at that practice's position in schema order, leaving
    every later practice unfiled while the heartbeat still reported.
    """
    b: PipelineHarness = practices["harnesses"]["practice_b"]
    b_claim = b.add(state="validated")

    def account_for(practice: PracticeContext) -> Any:
        if practice.schema == "practice_a":
            raise RuntimeError('relation "practice_billing_profiles" does not exist')
        return ACCOUNT

    monkeypatch.setattr(job, "_account_for", account_for)

    totals = job.run_pipeline()

    assert totals["practices"] == 2
    assert totals["practice_errors"] == 1
    # The practice after the failure still filed.
    assert b.get(b_claim.id).state == "submitted"


def test_the_fan_out_asks_for_real_practices_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody is owed money by a practice that exists to exercise a signup.

    The sweep is bounded, and costs a credential lookup plus a roster read
    per tenant before one can even be ruled out — so a suite that provisions
    a practice per run would otherwise crowd real practices out of the pass.
    """
    asked: dict[str, object] = {}

    def registry(_engine: object, *, include_pentest: bool = True) -> list[tuple[str, str]]:
        asked["include_pentest"] = include_pentest
        return []

    monkeypatch.setattr(fanout, "get_engine", object)
    monkeypatch.setattr(fanout, "list_active_practice_registry", registry)

    assert list(fanout.active_practices(max_tenants=10)) == []
    assert asked["include_pentest"] is False


def test_a_practice_that_cannot_be_resolved_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution raises inside the generator, out of reach of the caller's guard.

    ``active_practices`` resolves each practice with a credential lookup and a
    roster read. Both run while the generator is being advanced, so a raise
    there cannot be caught by a ``try`` in the consuming loop's body — it
    would truncate the iteration instead.
    """
    monkeypatch.setattr(fanout, "get_engine", object)
    monkeypatch.setattr(
        fanout,
        "list_active_practice_registry",
        lambda _engine, **_kwargs: [("practice_a", "a"), ("practice_b", "b")],
    )

    def client_for(practice_id: str | None) -> object:
        if practice_id == "a":
            raise RuntimeError("credential lookup failed")
        return object()

    monkeypatch.setattr(fanout, "clearinghouse_client_for_practice", client_for)
    monkeypatch.setattr(fanout, "practice_user_ids", lambda _practice_id: [USER_ID])

    resolved = list(fanout.active_practices(max_tenants=10))

    assert [practice.schema for practice in resolved] == ["practice_b"]


def test_a_later_stage_failing_cannot_undo_an_earlier_stage(
    practices: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filing that happened stays happened, even when the next stage dies.

    The stages share one clinician session, so they used to share one
    transaction: a raise in the status stage rolled back the claim the submit
    stage had already sent to the payer. The idempotency marker survived,
    because submit commits that deliberately before the vendor call, so the
    next run found a claim marked pending, reconciled it out of the feed, moved
    it, and lost it again — every interval, forever, and the practice could
    file nothing at all (PABLO-02vb).

    Asserting a commit lands BETWEEN the two stages is asserting that loop
    cannot form. The lock timeout that triggered it in production is not
    reproduced here on purpose: any exception did this, so the test raises the
    cheapest one.
    """
    from app.claims.submit_worker import SubmitSummary  # noqa: PLC0415

    harness: PipelineHarness = practices["harnesses"]["practice_a"]
    order: list[str] = []

    def submit_pending(*_args: Any, commit: Any, **_kwargs: Any) -> SubmitSummary:
        order.append("submit")
        return SubmitSummary(submitted=1)

    def poll_acknowledgments(*_args: Any, **_kwargs: Any) -> Any:
        order.append("status")
        msg = "lock timeout, or anything else"
        raise RuntimeError(msg)

    def commit() -> None:
        order.append("commit")

    monkeypatch.setattr(job, "submit_pending", submit_pending)
    monkeypatch.setattr(job, "poll_acknowledgments", poll_acknowledgments)
    monkeypatch.setattr(harness, "commit", commit)

    with pytest.raises(RuntimeError):
        job.run_practice(
            PracticeContext(
                schema="practice_a",
                practice_id="practice_a",
                client=harness.client,
                user_ids=[USER_ID],
            ),
            ["submit", "status"],
            max_per_tenant=10,
        )

    assert order == ["submit", "commit", "status"], (
        "the filing must be committed before the next stage can fail"
    )


def test_one_failing_clinician_does_not_stop_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = make_harness()
    try:
        practice = PracticeContext(
            schema="practice_a", practice_id="a", client=harness.client, user_ids=["u1", "u2"]
        )
        seen: list[str] = []

        class _Session:
            def __init__(self, user_id: str) -> None:
                self.user_id = user_id

            def commit(self) -> None:
                pass

        class _Ctx:
            def __init__(self, user_id: str) -> None:
                self.user_id = user_id

            def __enter__(self) -> _Session:
                return _Session(self.user_id)

            def __exit__(self, *_: object) -> bool:
                return False

        monkeypatch.setattr(fanout, "tenant_db_session", lambda _s, user_id: _Ctx(user_id))
        monkeypatch.setattr(fanout, "PostgresClaimRepository", lambda _s: harness.claims)
        monkeypatch.setattr(fanout, "PostgresClaimReceiptRepository", lambda _s: harness.receipts)
        monkeypatch.setattr(fanout, "PostgresPayerRepository", lambda _s: harness.payers)

        def work(run: TenantRun, user_id: str) -> None:
            seen.append(user_id)
            assert run.pipeline.principal_user_id == user_id
            if user_id == "u1":
                raise RuntimeError("boom")

        assert fanout.for_each_clinician(practice, work) == 1
        assert seen == ["u1", "u2"]
    finally:
        restore_listeners()
