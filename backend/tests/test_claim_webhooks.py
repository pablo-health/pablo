# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clearinghouse webhook: its signature contract and what it hands on.

Authentication is the bulk of it: a delivery with a missing, wrong, stale
or differently-bodied signature is 401 before the body is read; a body
that is not an event is 400; the vendor's ping is 200. The fan-out that
applies a transaction event across practices is exercised on fakes, with
the redelivery (same event id) proven a no-op.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

import pytest
from app.claims import fanout
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.claims.webhooks import WebhookEvent, parse_event, verify_signature
from app.routes import claim_webhooks
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.claims_fixtures import USER_ID
from tests.claims_pipeline_fakes import (
    NOW,
    PipelineHarness,
    fixture,
    make_harness,
    restore_listeners,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# Deliberately not shaped like a real signing secret: these are only ever fed
# to hmac.new(), so any bytes do.
_SECRET = "webhook-signing-secret-for-tests"
_OLD_SECRET = "previous-webhook-signing-secret-for-tests"
_TRANSACTION = "7cfce085-f4af-43ce-aa36-5654d52efec0"


_MESSAGE_ID = "msg_2f5a1c1a01"


def _signing_key(secret: str) -> bytes:
    """Built the way the vendor's scheme (and the fake clearinghouse) builds it."""
    if secret.startswith("whsec_"):
        return base64.b64decode(secret.removeprefix("whsec_"))
    return secret.encode()


def _sign(
    body: bytes,
    secret: str = _SECRET,
    *,
    timestamp: int | None = None,
    message_id: str = _MESSAGE_ID,
) -> dict[str, str]:
    """Standard Webhooks headers: HMAC-SHA256 over ``"<id>.<timestamp>.<body>"``."""
    stamp = int(time.time()) if timestamp is None else timestamp
    signed = f"{message_id}.{stamp}.".encode() + body
    digest = hmac.new(_signing_key(secret), signed, hashlib.sha256).digest()
    return {
        "webhook-id": message_id,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
        "webhook-timestamp": str(stamp),
    }


def _verify(body: bytes, headers: dict[str, str], secrets: list[str], **kwargs: Any) -> bool:
    return verify_signature(
        body,
        signature_header=headers.get("webhook-signature"),
        timestamp_header=headers.get("webhook-timestamp"),
        message_id=headers.get("webhook-id"),
        secrets=secrets,
        **kwargs,
    )


def _event(**overrides: Any) -> bytes:
    payload = {**fixture("webhook_transaction_processed.json"), **overrides}
    return json.dumps(payload).encode()


# --- verify_signature -----------------------------------------------------------


def test_a_standard_webhooks_delivery_verifies() -> None:
    body = _event()
    assert _verify(body, _sign(body), [_SECRET])


def test_a_known_vector_verifies() -> None:
    """A fixed delivery, signed by hand the way the vendor's scheme says.

    ``echo -n "msg_vector.1700000000.{}" | openssl dgst -sha256 -hmac k | base64``
    """
    body = b"{}"
    headers = {
        "webhook-id": "msg_vector",
        "webhook-timestamp": "1700000000",
        "webhook-signature": "v1,"
        + base64.b64encode(
            hmac.new(b"k", b"msg_vector.1700000000.{}", hashlib.sha256).digest()
        ).decode(),
    }
    assert _verify(body, headers, ["k"], now=1700000001)
    assert not _verify(body, {**headers, "webhook-id": "msg_other"}, ["k"], now=1700000001)


def test_a_prefixed_base64_secret_is_decoded_before_signing() -> None:
    body = _event()
    raw_key = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(raw_key).decode()
    headers = _sign(body, secret, timestamp=1700000000)
    assert _verify(body, headers, [secret], now=1700000010)
    # Signing with the prefixed string itself is not the same key.
    digest = hmac.new(secret.encode(), b"msg_x.1700000000." + body, hashlib.sha256).digest()
    wrong = {
        "webhook-id": "msg_x",
        "webhook-timestamp": "1700000000",
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
    }
    assert not _verify(body, wrong, [secret], now=1700000010)


def test_the_signature_without_the_delivery_id_is_refused() -> None:
    """``timestamp.body`` alone is not the scheme; the id is part of what is signed."""
    body = _event()
    stamp = str(int(time.time()))
    digest = hmac.new(_SECRET.encode(), f"{stamp}.".encode() + body, hashlib.sha256).digest()
    headers = {
        "webhook-id": _MESSAGE_ID,
        "webhook-timestamp": stamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
    }
    assert not _verify(body, headers, [_SECRET])


def test_a_delivery_without_an_id_header_is_refused() -> None:
    body = _event()
    headers = _sign(body)
    del headers["webhook-id"]
    assert not _verify(body, headers, [_SECRET])


def test_several_signatures_in_the_header_are_all_tried() -> None:
    body = _event()
    headers = _sign(body)
    headers["webhook-signature"] = "v1,AAAA " + headers["webhook-signature"]
    assert _verify(body, headers, [_SECRET])


@pytest.mark.parametrize(
    ("signature", "timestamp", "secrets", "now"),
    [
        (None, "1700000000", [_SECRET], 1700000000),
        ("v1,AAAA", None, [_SECRET], 1700000000),
        ("garbage", "1700000000", [_SECRET], 1700000000),
        ("v1,AAAA", "not-a-number", [_SECRET], 1700000000),
        ("v1,AAAA", "1700000000", [], 1700000000),
    ],
)
def test_malformed_or_unconfigured_is_refused(
    signature: str | None, timestamp: str | None, secrets: list[str], now: float
) -> None:
    assert not verify_signature(
        _event(),
        signature_header=signature,
        timestamp_header=timestamp,
        message_id=None,
        secrets=secrets,
        now=now,
    )


def test_a_stale_timestamp_is_refused_even_when_the_signature_is_right() -> None:
    body = _event()
    headers = _sign(body, timestamp=1700000000)
    assert not _verify(body, headers, [_SECRET], now=1700000000 + 600)


def test_parse_event_reads_the_transaction_reference() -> None:
    event = parse_event(json.loads(_event()), delivery_id="msg_1")
    assert event == WebhookEvent(
        id="msg_1", type="transaction.processed", transaction_id=_TRANSACTION
    )
    assert parse_event({"id": "evt"}, delivery_id="msg_1") is None
    assert parse_event(["not", "an", "object"], delivery_id="msg_1") is None
    assert parse_event({"id": "e", "type": "file.processed"}, delivery_id="msg_1") == (
        WebhookEvent(id="msg_1", type="file.processed", transaction_id=None)
    )


def test_parse_event_reads_the_newer_delivery_shape() -> None:
    """``detail-type`` with a version suffix, and the transaction document under ``detail``."""
    payload = {
        "id": "evt_9",
        "source": "stedi.core",
        "detail-type": "transaction.processed.v2",
        "detail": fixture("transaction_inbound_277.json"),
    }
    assert parse_event(payload, delivery_id="msg_9") == WebhookEvent(
        id="msg_9", type="transaction.processed", transaction_id=_TRANSACTION
    )


# --- the route ---------------------------------------------------------------------


class _Settings:
    def __init__(self, current: str = _SECRET, previous: str = "") -> None:
        self.clearinghouse_webhook_secret = SecretStr(current)
        self.clearinghouse_webhook_secret_previous = SecretStr(previous)


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    settings = _Settings()
    ingested: list[WebhookEvent] = []
    outcome: dict[str, Any] = {"value": "moved"}

    def ingest(event: WebhookEvent) -> str:
        ingested.append(event)
        if isinstance(outcome["value"], Exception):
            raise outcome["value"]
        return str(outcome["value"])

    monkeypatch.setattr(claim_webhooks, "get_settings", lambda: settings)
    monkeypatch.setattr(claim_webhooks, "ingest_transaction_event", ingest)
    app = FastAPI()
    app.include_router(claim_webhooks.router)
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "settings": settings,
        "ingested": ingested,
        "outcome": outcome,
    }


def _post(route: dict[str, Any], body: bytes, headers: dict[str, str]) -> Any:
    return route["client"].post(
        claim_webhooks.CLEARINGHOUSE_WEBHOOK_PATH, content=body, headers=headers
    )


def test_missing_signature_is_401(route: dict[str, Any]) -> None:
    assert _post(route, _event(), {}).status_code == 401
    assert route["ingested"] == []


def test_wrong_secret_is_401(route: dict[str, Any]) -> None:
    body = _event()
    assert _post(route, body, _sign(body, "some-other-secret")).status_code == 401


def test_a_signature_over_a_different_body_is_401(route: dict[str, Any]) -> None:
    headers = _sign(_event())
    assert _post(route, _event(id="evt_other"), headers).status_code == 401


def test_a_stale_timestamp_is_401(route: dict[str, Any]) -> None:
    body = _event()
    assert _post(route, body, _sign(body, timestamp=int(time.time()) - 900)).status_code == 401


def test_the_previous_secret_is_accepted_during_rotation(route: dict[str, Any]) -> None:
    route["settings"].clearinghouse_webhook_secret_previous = SecretStr(_OLD_SECRET)
    body = _event()
    assert _post(route, body, _sign(body, _OLD_SECRET)).status_code == 200


def test_an_unconfigured_secret_rejects_everything(route: dict[str, Any]) -> None:
    route["settings"].clearinghouse_webhook_secret = SecretStr("")
    body = _event()
    assert _post(route, body, _sign(body)).status_code == 401


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"type": "transaction.processed"}'])
def test_a_malformed_body_is_400(route: dict[str, Any], body: bytes) -> None:
    assert _post(route, body, _sign(body)).status_code == 400
    assert route["ingested"] == []


def test_a_transaction_event_without_a_resource_is_400(route: dict[str, Any]) -> None:
    body = _event(resource={"type": "file", "id": "f-1"})
    assert _post(route, body, _sign(body)).status_code == 400


def test_the_vendors_ping_is_answered(route: dict[str, Any]) -> None:
    body = _event(type="event.ping", resource=None)
    response = _post(route, body, _sign(body))
    assert response.status_code == 200
    assert response.json()["outcome"] == "ping"
    assert route["ingested"] == []


def test_other_event_types_are_acknowledged_and_ignored(route: dict[str, Any]) -> None:
    body = _event(type="file.processed")
    response = _post(route, body, _sign(body))
    assert response.status_code == 200
    assert response.json()["outcome"] == "ignored"
    assert route["ingested"] == []


def test_a_transaction_event_is_handed_on_keyed_by_its_delivery_id(
    route: dict[str, Any],
) -> None:
    body = _event()
    response = _post(route, body, _sign(body, message_id="msg_1"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "outcome": "moved"}
    [event] = route["ingested"]
    assert event.id == "msg_1"
    assert event.transaction_id == _TRANSACTION


def test_the_fake_clearinghouses_delivery_shape_is_handed_on(route: dict[str, Any]) -> None:
    """The e2e harness signs the same way and sends the newer body shape."""
    payload = {
        "id": "evt_fake",
        "source": "stedi.core",
        "detail-type": "transaction.processed",
        "time": "2026-09-06T15:51:22Z",
        "detail": fixture("transaction_inbound_277.json"),
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    secret = "whsec_" + base64.b64encode(b"fake-clearinghouse-key-bytes").decode()
    route["settings"].clearinghouse_webhook_secret = SecretStr(secret)

    response = _post(route, body, _sign(body, secret, message_id="evt_fake"))

    assert response.status_code == 200, response.text
    [event] = route["ingested"]
    assert (event.id, event.transaction_id) == ("evt_fake", _TRANSACTION)


def test_a_vendor_outage_asks_for_a_redelivery(route: dict[str, Any]) -> None:
    route["outcome"]["value"] = ClearinghouseUnavailableError("down")
    body = _event()
    assert _post(route, body, _sign(body)).status_code == 503


# --- the fan-out over practices ------------------------------------------------------


@pytest.fixture
def practices(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[PipelineHarness]]:
    """Two practices on fakes; the tenant session and the repositories are stubbed."""
    harnesses = [make_harness(now=NOW, principal=USER_ID), make_harness(now=NOW, principal="b-1")]
    contexts = [
        fanout.PracticeContext(
            schema=f"practice_{i}",
            practice_id=f"p{i}",
            client=harness.client,
            user_ids=[harness.pipeline.principal_user_id],
        )
        for i, harness in enumerate(harnesses)
    ]
    by_schema = dict(zip([c.schema for c in contexts], harnesses, strict=True))
    current: dict[str, PipelineHarness] = {}

    @contextlib.contextmanager
    def tenant_db_session(schema: str, user_id: str) -> Iterator[object]:
        harness = by_schema[schema]
        assert user_id == harness.pipeline.principal_user_id
        current["harness"] = harness
        yield object()

    monkeypatch.setattr(fanout, "active_practices", lambda **_kwargs: iter(contexts))
    monkeypatch.setattr(fanout, "tenant_db_session", tenant_db_session)
    monkeypatch.setattr(fanout, "PostgresClaimRepository", lambda _s: current["harness"].claims)
    monkeypatch.setattr(
        fanout, "PostgresClaimReceiptRepository", lambda _s: current["harness"].receipts
    )
    # Every harness registered its own recording listener; the last one wins,
    # so hand every practice the same one to assert against.
    for harness in harnesses:
        harness.listener = harnesses[-1].listener
    yield harnesses
    restore_listeners()


def test_the_practice_that_owns_the_transaction_applies_it(
    practices: list[PipelineHarness],
) -> None:
    first, second = practices
    created = second.add(state="submitted", submitted_at=NOW)
    transaction = second.client.acknowledge("payer_accepted", created.control_number)

    outcome = fanout.ingest_transaction_event(
        WebhookEvent(id="evt-1", type="transaction.processed", transaction_id=transaction)
    )

    assert outcome == "moved"
    assert second.get(created.id).state == "payer_accepted"
    assert first.receipts.list_for_claim(created.id) == []


def test_a_redelivery_is_a_no_op_with_no_second_transition(
    practices: list[PipelineHarness],
) -> None:
    first, _ = practices
    created = first.add(state="submitted", submitted_at=NOW)
    transaction = first.client.acknowledge("payer_rejected", created.control_number)
    event = WebhookEvent(id="evt-1", type="transaction.processed", transaction_id=transaction)

    assert fanout.ingest_transaction_event(event) == "moved"
    assert fanout.ingest_transaction_event(event) == "duplicate"

    assert first.get(created.id).state == "rejected"
    assert len(first.receipts.list_for_claim(created.id)) == 1
    assert first.listener.kinds() == ["rejected"]


def test_a_transaction_naming_no_claim_of_ours_is_unmatched(
    practices: list[PipelineHarness],
) -> None:
    first, _ = practices
    transaction = first.client.acknowledge("payer_accepted", "SOMEBODYELSE")

    outcome = fanout.ingest_transaction_event(
        WebhookEvent(id="evt-1", type="transaction.processed", transaction_id=transaction)
    )

    assert outcome == "unmatched"


def test_a_document_that_is_not_a_277_is_ignored(practices: list[PipelineHarness]) -> None:
    first, _ = practices
    transaction = first.client.filed("ANY")

    outcome = fanout.ingest_transaction_event(
        WebhookEvent(id="evt-1", type="transaction.processed", transaction_id=transaction)
    )

    assert outcome == "ignored"
