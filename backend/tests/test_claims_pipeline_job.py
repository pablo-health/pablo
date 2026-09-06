# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The scheduled pipeline (``app.jobs.claims_pipeline``): the fan-out and its bounds.

Each practice is visited once, each of its clinicians in their own unit of
work, the stages in order; ``--max-tenants`` bounds the run; a clinician
whose unit of work fails does not stop the next one.
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
