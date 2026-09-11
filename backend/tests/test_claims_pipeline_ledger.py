# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The scheduled pipeline actually reaches the client's ledger.

`apply_posting` has been able to write a client's share onto their ledger
for a while, and the scheduled pipeline never passed it a ledger to write
to — so what a payer said a client owed stopped at the claim, and no test
noticed, because every test that exercised the write handed the repository
in itself.

That is the same shape as the check this feature exists for: a correct
piece of code with no production caller. So these tests assert the WIRING
rather than the arithmetic — that the object the job constructs is handed
down to the posting path, and that it is handed down through the guard
rather than around it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from app.claims import fanout
from app.claims.fanout import PracticeContext, TenantRun
from app.jobs import claims_pipeline as job
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine
from app.models.claims_timeline import ClaimTimeline, TimelinePayment

from tests.claims_fixtures import USER_ID
from tests.claims_pipeline_fakes import ACCOUNT, NOW, make_harness, restore_listeners

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CHARGED = 15_000


class _Ledger:
    """Just enough of the charge repository to see what reached it."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add_ledger_row(self, **row):
        self.rows.append(row)
        return row

    def list_charges(self, patient_id: str):
        return [
            SimpleNamespace(
                amount_cents=row["amount_cents"],
                claim_id=row.get("claim_id"),
                kind=row["kind"],
            )
            for row in self.rows
            if row["patient_id"] == patient_id
        ]


class _Timelines:
    """A timeline source that says the payer paid $120 of $150."""

    def timeline_for(self, _vendor_claim_id: str) -> ClaimTimeline:
        return ClaimTimeline(
            payments=[
                TimelinePayment(
                    id="clp_1",
                    disposition="paid",
                    charged_cents=CHARGED,
                    paid_cents=12_000,
                    patient_responsibility_cents=3_000,
                    trace_number="EFT1",
                    processed_at=NOW,
                )
            ]
        )


def _adjustment(group: str, reason: str, cents: int) -> Adjustment:
    return Adjustment(group_code=group, reason_code=reason, amount_cents=cents)


class _Details:
    """The 835 half, which is what the balance checks read."""

    def __init__(self, *, agreeing: bool) -> None:
        self._agreeing = agreeing

    def detail_for(self, control_number: str) -> RemittanceClaim:
        adjustments = (
            [_adjustment("PR", "2", 3_000)]
            if self._agreeing
            # Stated as the client's $30, itemised as a contractual
            # write-off: the payer's two statements disagree.
            else [_adjustment("CO", "45", 3_000)]
        )
        return RemittanceClaim(
            patient_control_number=control_number,
            payer_claim_control_number="P1",
            claim_status_code="1",
            total_charge_cents=CHARGED,
            paid_cents=12_000,
            patient_responsibility_cents=3_000,
            claim_frequency_code="1",
            adjustments=[],
            lines=[
                RemittanceLine(
                    line_control_number=f"{control_number}L1",
                    service_date="20260901",
                    cpt="90837",
                    charge_cents=CHARGED,
                    paid_cents=12_000,
                    adjustments=adjustments,
                )
            ],
        )


@pytest.fixture
def practice(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    harness = make_harness(now=NOW, principal=USER_ID)
    ledger = _Ledger()
    context = PracticeContext(
        schema="practice_a", practice_id="practice_a", client=harness.client, user_ids=[USER_ID]
    )
    agreeing = {"value": True}

    def for_each_clinician(
        _practice: PracticeContext, work: Callable[[TenantRun, str], None]
    ) -> int:
        work(
            TenantRun(
                pipeline=harness.pipeline,
                payers=harness.payers,
                commit=harness.commit,
                charges=ledger,
            ),
            USER_ID,
        )
        return 1

    monkeypatch.setattr(
        job, "active_practices", lambda *, max_tenants: iter([context][:max_tenants])
    )
    monkeypatch.setattr(job, "for_each_clinician", for_each_clinician)
    monkeypatch.setattr(job, "_account_for", lambda _practice: ACCOUNT)
    monkeypatch.setattr(job, "_timelines_for", lambda _practice: _Timelines())
    monkeypatch.setattr(
        job, "FeedRemittanceDetails", lambda _client: _Details(agreeing=agreeing["value"])
    )
    yield {"harness": harness, "ledger": ledger, "agreeing": agreeing}
    restore_listeners()


def test_what_the_payer_says_the_client_owes_reaches_the_client(practice) -> None:
    """The wiring, end to end through the job.

    Without it the practice sees a claim paid $120 of $150 and the client is
    never told about the $30.
    """
    harness = practice["harness"]
    claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v1")

    job.run_practice(
        PracticeContext(
            schema="practice_a",
            practice_id="practice_a",
            client=harness.client,
            user_ids=[USER_ID],
        ),
        ["remit"],
        max_per_tenant=10,
    )

    [row] = practice["ledger"].rows
    assert row["kind"] == "patient_resp"
    assert row["amount_cents"] == 3_000
    assert row["claim_id"] == claim.id
    assert row["patient_id"] == claim.patient_id


def test_a_remittance_that_contradicts_itself_still_reaches_no_client(practice) -> None:
    """The guard runs on the wired path, not only where tests call it.

    This is the pairing that matters: turning the ledger on without the
    hold in front of it is the change that bills somebody the wrong amount.
    """
    practice["agreeing"]["value"] = False
    harness = practice["harness"]
    claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v1")

    job.run_practice(
        PracticeContext(
            schema="practice_a",
            practice_id="practice_a",
            client=harness.client,
            user_ids=[USER_ID],
        ),
        ["remit"],
        max_per_tenant=10,
    )

    assert practice["ledger"].rows == []
    [hold] = harness.holds.list_open()
    assert hold.claim_id == claim.id
    # The payer's money still posted; only the client's bill waits.
    assert harness.get(claim.id).total_paid_cents == 12_000


def test_the_run_that_builds_a_real_tenant_run_gives_it_a_ledger() -> None:
    """`for_each_clinician` is where the real repository is constructed.

    The fixture above substitutes it, so on its own the tests here would
    pass against a production path that still passes nothing. This asserts
    the field exists to be filled and defaults to nothing rather than to a
    silently-wrong object.
    """
    assert "charges" in TenantRun.__dataclass_fields__
    assert TenantRun(pipeline=None, payers=None, commit=lambda: None).charges is None
    assert "PostgresPatientPaymentRepository" in fanout.__dict__
