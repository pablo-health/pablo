# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The fake clearinghouse's native claim endpoint, driven by the real SDK.

Every other test of ``StediClearinghouseClient.submit_claim`` mocks the SDK
client itself, so none of them would notice if the vendor's SDK sent a
request the fake could not answer — which is exactly what happened when the
adapter moved onto the SDK's native claim API and the fake kept only the
compatibility shim's route. This test does not mock the SDK: it starts the
fake app on a real loopback socket and lets the SDK's own HTTP client reach
it, the way the end-to-end stack does.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uvicorn
from app.claims.clearinghouse import ClearinghouseRequestChangedError
from app.claims.credentials import ClearinghouseCredentials
from app.claims.stedi import StediClearinghouseClient
from app.models.claims_transport import ClaimSubmissionRequest

from scripts.fake_clearinghouse import app, state

if TYPE_CHECKING:
    from collections.abc import Iterator

_FIXTURE = Path(__file__).parent / "fixtures" / "clearinghouse" / "837p_request_test_payer.json"


def _submission_request(*, control_number: str | None = None) -> ClaimSubmissionRequest:
    body = json.loads(_FIXTURE.read_text())
    if control_number is not None:
        body["claimInformation"]["patientControlNumber"] = control_number
    return ClaimSubmissionRequest.model_validate(body)


@pytest.fixture
def fake_clearinghouse() -> Iterator[str]:
    """The fake clearinghouse, bound to a free loopback port."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    state.reset()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        state.reset()


def _client(origin: str) -> StediClearinghouseClient:
    credentials = ClearinghouseCredentials(api_key="test_placeholder", mode="test", base_url=origin)
    return StediClearinghouseClient(credentials)


class TestNativeSubmission:
    def test_a_filed_claim_is_accepted_with_a_native_claim_id(
        self, fake_clearinghouse: str
    ) -> None:
        client = _client(fake_clearinghouse)

        result = client.submit_claim(_submission_request(), idempotency_key="attempt-1")

        assert result.status == "SUCCESS"
        assert result.claimReference is not None
        assert result.claimReference.correlationId

    def test_resubmitting_the_same_control_number_keeps_the_claim_id(
        self, fake_clearinghouse: str
    ) -> None:
        client = _client(fake_clearinghouse)
        control_number = "resub-0001"

        first = client.submit_claim(
            _submission_request(control_number=control_number), idempotency_key="attempt-1"
        )
        second = client.submit_claim(
            _submission_request(control_number=control_number), idempotency_key="attempt-2"
        )

        assert first.claimReference is not None
        assert second.claimReference is not None
        assert first.claimReference.correlationId == second.claimReference.correlationId

    def test_the_same_idempotency_key_and_body_replays_without_a_new_timer(
        self, fake_clearinghouse: str
    ) -> None:
        client = _client(fake_clearinghouse)
        req = _submission_request(control_number="idem-0001")

        first = client.submit_claim(copy.deepcopy(req), idempotency_key="same-key")
        pending_after_first = len(state.timers)
        second = client.submit_claim(copy.deepcopy(req), idempotency_key="same-key")

        assert first.claimReference is not None
        assert second.claimReference is not None
        assert first.claimReference.correlationId == second.claimReference.correlationId
        assert len(state.timers) == pending_after_first

    def test_the_same_idempotency_key_with_a_changed_body_is_refused(
        self, fake_clearinghouse: str
    ) -> None:
        client = _client(fake_clearinghouse)
        first_req = _submission_request(control_number="idem-0002")
        changed_req = _submission_request(control_number="idem-0002")
        changed_req.claimInformation.claimChargeAmount = "999.00"

        client.submit_claim(first_req, idempotency_key="reused-key")

        with pytest.raises(ClearinghouseRequestChangedError):
            client.submit_claim(changed_req, idempotency_key="reused-key")

    @pytest.mark.parametrize(
        "control_number",
        ["REJ-DX-0001", "REJ-PTR-0001", "REJ-SUB-0001"],
    )
    def test_control_number_rules_still_produce_edit_rejections(
        self, fake_clearinghouse: str, control_number: str
    ) -> None:
        client = _client(fake_clearinghouse)

        result = client.submit_claim(
            _submission_request(control_number=control_number), idempotency_key="attempt-1"
        )

        assert result.status == "ERROR"
        assert result.errors
        assert all(error.description for error in result.errors)

    def test_the_legacy_endpoints_the_httpx_adapter_uses_still_answer(
        self, fake_clearinghouse: str
    ) -> None:
        client = _client(fake_clearinghouse)

        payers = client.search_payers("Stedi Test Payer")

        assert payers
