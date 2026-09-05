# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the card-payment webhook (``app.routes.payment_webhooks``).

Most of these are about the authentication and idempotency contract: a request
with a bad, missing, stale or tampered signature is rejected with 401 before
the body is parsed, a malformed body is 400, and a redelivered event id is a
no-op that never touches a practice schema.

The rest cover the reconciliation the endpoint exists for: a success that
arrives after the synchronous call gave up, a failure carrying its decline
code, and a refund issued in the practice's own dashboard (the only way the
ledger learns about one).

The most consequential test here is the one that asserts an event we could NOT
reconcile is left unrecorded and answered with 503. Recording it would spend
the processor's redelivery on a failure and strand the ledger row at
``pending`` forever, which is a money-visible bug nothing else would catch.

Hermetic: the platform session and the practice engine are faked, so the SQL
the handler would issue is captured and asserted rather than executed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from app.db.platform_models import ProcessedPaymentEventRow
from app.payments import reconcile
from app.routes import payment_webhooks
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

_SECRET = "whsec_test_secret"
_OLD_SECRET = "whsec_previous_secret"
_PRACTICE_ID = "practice-1"
_SCHEMA = "practice_abc123"
_USER_ID = "user-1"
_CHARGE_ID = "charge-1"


class _Settings:
    """Only the fields the receiver reads."""

    def __init__(self, current: str = _SECRET, previous: str = "") -> None:
        self.stripe_webhook_secret = SecretStr(current)
        self.stripe_webhook_secret_previous = SecretStr(previous)
        self.multi_tenancy_enabled = True


class _FakeConn:
    """Records every statement the handler issues on the practice connection.

    ``current_status`` is what the ``SELECT ... FOR UPDATE`` sees; ``None``
    stands for "no ledger row is visible for this PaymentIntent" (it is not
    there, or the row policy refused it), which is the case that must NOT be
    recorded as processed. ``row_owner`` is what the UPDATE's ``RETURNING``
    hands back — the attribution the handler checks the event's claim against.
    """

    def __init__(
        self,
        current_status: str | None = "pending",
        *,
        updated: bool = True,
        row_owner: str = _USER_ID,
    ) -> None:
        self.statements: list[tuple[str, dict[str, Any] | None]] = []
        self.current_status = current_status
        self.updated = updated
        self.row_owner = row_owner

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, params))

        if "SELECT status FROM patient_charges" in sql:
            row = (self.current_status,) if self.current_status is not None else None
            return SimpleNamespace(first=lambda: row)

        if "UPDATE patient_charges" in sql:
            row = (self.row_owner,) if self.updated else None
            return SimpleNamespace(first=lambda: row)

        return SimpleNamespace(first=lambda: None)


class _FakeEngine:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def begin(self) -> Any:
        conn = self._conn

        class _Ctx:
            def __enter__(self) -> _FakeConn:
                return conn

            def __exit__(self, *_: Any) -> bool:
                return False

        return _Ctx()


class _FakePractice:
    def __init__(self) -> None:
        self.id = _PRACTICE_ID
        self.schema_name = _SCHEMA
        self.is_active = True
        self.deleted_at = None


class _FakePlatformSession:
    """The platform-scoped session: practice lookup plus the dedupe ledger."""

    def __init__(self, *, practice: _FakePractice | None, processed_ids: set[str]) -> None:
        self._practice = practice
        self.processed_ids = processed_ids
        self.added: list[Any] = []
        self.commits = 0

    def __enter__(self) -> _FakePlatformSession:
        return self

    def __exit__(self, *_: Any) -> bool:
        return False

    def get(self, model: type, key: Any) -> Any:
        if model is ProcessedPaymentEventRow:
            return object() if key in self.processed_ids else None
        return self._practice

    def add(self, row: Any) -> None:
        self.added.append(row)
        self.processed_ids.add(row.event_id)

    def commit(self) -> None:
        self.commits += 1


def _sign(body: bytes, secret: str = _SECRET, *, timestamp: int | None = None) -> str:
    stamp = int(time.time()) if timestamp is None else timestamp
    payload = f"{stamp}.".encode() + body
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _event(event_type: str, obj: dict[str, Any], *, event_id: str = "evt_1") -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "created": 1_760_000_000,
        "data": {"object": obj},
    }


def _ours(obj: dict[str, Any]) -> dict[str, Any]:
    """Stamp the metadata the charge route puts on every PaymentIntent.

    Stripe copies it onto the charge too, so a ``charge.refunded`` event for one
    of our charges carries it exactly like a ``payment_intent.*`` one.
    """
    return {
        **obj,
        "metadata": {
            "pablo_charge_id": _CHARGE_ID,
            "pablo_user_id": _USER_ID,
            "pablo_practice_id": _PRACTICE_ID,
        },
    }


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Wire the handler onto fakes and hand back the pieces to assert on.

    An event built WITHOUT ``_ours(...)`` carries no metadata of ours, which is
    what a charge the practice raised in its own Stripe dashboard looks like.
    """
    conn = _FakeConn()
    session = _FakePlatformSession(practice=_FakePractice(), processed_ids=set())
    settings = _Settings()
    monkeypatch.setattr(payment_webhooks, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "get_settings", lambda: settings)
    monkeypatch.setattr(reconcile, "get_engine", lambda: _FakeEngine(conn))
    monkeypatch.setattr(reconcile, "create_standalone_session", lambda: session)

    app = FastAPI()
    app.include_router(payment_webhooks.router)
    return {
        "client": TestClient(app, raise_server_exceptions=False),
        "conn": conn,
        "session": session,
        "settings": settings,
    }


def _post(harness: dict[str, Any], event: dict[str, Any], *, signature: str | None = None) -> Any:
    body = json.dumps(event).encode()
    headers = {"content-type": "application/json"}
    if signature is None:
        signature = _sign(body)
    if signature != "":
        headers["Stripe-Signature"] = signature
    return harness["client"].post(
        payment_webhooks.PAYMENT_WEBHOOK_PATH, content=body, headers=headers
    )


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignature:
    def test_missing_signature_is_401(self, harness: dict[str, Any]) -> None:
        response = _post(harness, _event("payment_intent.succeeded", {"id": "pi_1"}), signature="")
        assert response.status_code == 401
        assert harness["conn"].statements == []

    def test_bad_signature_is_401(self, harness: dict[str, Any]) -> None:
        response = _post(
            harness,
            _event("payment_intent.succeeded", {"id": "pi_1"}),
            signature="t=1760000000,v1=deadbeef",
        )
        assert response.status_code == 401
        assert harness["conn"].statements == []

    def test_a_signature_over_a_different_body_is_401(self, harness: dict[str, Any]) -> None:
        """Tampering with the body after signing must not verify."""
        signed_for = json.dumps(_event("payment_intent.succeeded", {"id": "pi_other"})).encode()
        response = _post(
            harness,
            _event("payment_intent.succeeded", {"id": "pi_1"}),
            signature=_sign(signed_for),
        )
        assert response.status_code == 401

    def test_a_stale_timestamp_is_401(self, harness: dict[str, Any]) -> None:
        """A replay of an old but correctly signed delivery is refused."""
        event = _event("payment_intent.succeeded", {"id": "pi_1"})
        body = json.dumps(event).encode()
        old = int(time.time()) - (reconcile.SIGNATURE_TOLERANCE_SECONDS + 60)
        response = _post(harness, event, signature=_sign(body, timestamp=old))
        assert response.status_code == 401

    def test_an_unconfigured_secret_rejects_everything(self, harness: dict[str, Any]) -> None:
        harness["settings"].stripe_webhook_secret = SecretStr("")
        response = _post(harness, _event("payment_intent.succeeded", {"id": "pi_1"}))
        assert response.status_code == 401

    def test_the_previous_secret_is_accepted_during_rotation(self, harness: dict[str, Any]) -> None:
        harness["settings"].stripe_webhook_secret_previous = SecretStr(_OLD_SECRET)
        event = _event("payment_intent.succeeded", _ours({"id": "pi_ours"}))
        body = json.dumps(event).encode()
        response = _post(harness, event, signature=_sign(body, secret=_OLD_SECRET))
        assert response.status_code == 200

    def test_a_malformed_body_is_400(self, harness: dict[str, Any]) -> None:
        body = b"not json at all"
        response = harness["client"].post(
            payment_webhooks.PAYMENT_WEBHOOK_PATH,
            content=body,
            headers={"Stripe-Signature": _sign(body), "content-type": "application/json"},
        )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Idempotency — and the one case that is deliberately not recorded
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_a_duplicate_event_id_is_a_no_op(self, harness: dict[str, Any]) -> None:
        harness["session"].processed_ids.add("evt_dupe")
        event = _event("payment_intent.succeeded", _ours({"id": "pi_ours"}), event_id="evt_dupe")

        response = _post(harness, event)

        assert response.status_code == 200
        assert response.json()["deduped"] == "true"
        assert harness["conn"].statements == []

    def test_an_applied_event_is_recorded(self, harness: dict[str, Any]) -> None:
        response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 200
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]
        assert harness["session"].added[0].practice_id == _PRACTICE_ID

        # Armed from the event's user id so the row policy admits the write.
        armed = next(
            params
            for sql, params in harness["conn"].statements
            if "app.current_user_id" in sql and params is not None
        )
        assert armed["uid"] == _USER_ID

    def test_a_foreign_charge_is_recorded_and_200s(self, harness: dict[str, Any]) -> None:
        """A charge the practice raised in its own dashboard, through a payment
        link, or on an invoice carries none of our metadata. That is ordinary
        traffic, not an error: a non-2xx here would have the processor retrying
        for days and eventually disabling the endpoint, which would stop the
        real charges reconciling too."""
        response = _post(harness, _event("payment_intent.succeeded", {"id": "pi_theirs"}))

        assert response.status_code == 200
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]
        assert harness["session"].added[0].practice_id is None
        # Not ours, so no practice schema is touched.
        assert harness["conn"].statements == []

    def test_a_refund_of_a_foreign_charge_is_recorded_and_200s(
        self, harness: dict[str, Any]
    ) -> None:
        """Same rule via the charge object: the processor copies PaymentIntent
        metadata onto the charge, so a refund with none was never ours."""
        response = _post(
            harness, _event("charge.refunded", {"id": "ch_theirs", "payment_intent": "pi_theirs"})
        )

        assert response.status_code == 200
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]
        assert harness["conn"].statements == []

    def test_an_unmatched_ledger_row_is_not_recorded_and_asks_for_a_retry(
        self, harness: dict[str, Any]
    ) -> None:
        """The event carries our metadata, so the charge IS ours — and the
        ledger row is not there. A genuine anomaly. The processor took the
        money; recording this event would leave the row at ``pending`` forever
        with only a log line to show for it. The redelivery is the safety net,
        so do not spend it on a failure."""
        harness["conn"].current_status = None

        response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 503
        assert harness["session"].added == []

    def test_an_update_matching_nothing_is_not_recorded(self, harness: dict[str, Any]) -> None:
        """The guard admitted the row a statement ago under FOR UPDATE, so an
        UPDATE returning nothing is an anomaly, not a stale transition."""
        harness["conn"].updated = False

        response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 503
        assert harness["session"].added == []

    def test_an_unreconciled_charge_logs_the_alertable_marker(
        self, harness: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        harness["conn"].current_status = None

        with caplog.at_level("ERROR", logger=reconcile.logger.name):
            _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert "charge_unreconciled" in caplog.text
        # Processor ids only — no client id, no amount.
        assert "pi_ours" in caplog.text

    def test_an_attribution_mismatch_still_applies_and_records_but_alerts(
        self, harness: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        """The event claimed one clinician; the row actually updated was created
        by another. The write STANDS and the event IS recorded — the row was
        pinned by the signed PaymentIntent id inside a schema resolved from the
        signed practice id, so the ledger outcome is right either way and a
        retry would change nothing — but it alerts, because the event's claim
        about WHO disagreed with our own record."""
        harness["conn"].row_owner = "user-someone-else"

        with caplog.at_level("ERROR", logger=reconcile.logger.name):
            response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 200
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]
        assert any("UPDATE patient_charges" in sql for sql, _ in harness["conn"].statements)
        assert "charge_unreconciled" in caplog.text

    def test_matching_attribution_does_not_alert(
        self, harness: dict[str, Any], caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("ERROR", logger=reconcile.logger.name):
            response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 200
        assert "charge_unreconciled" not in caplog.text

    def test_a_stale_transition_is_recorded_and_not_retried(self, harness: dict[str, Any]) -> None:
        """A late failure against an already-succeeded row is the status guard
        doing its job, not a failure — a retry could only produce the same
        refusal, so this one DOES record."""
        harness["conn"].current_status = "succeeded"
        event = _event(
            "payment_intent.payment_failed",
            _ours({"id": "pi_ours", "last_payment_error": {"decline_code": "insufficient_funds"}}),
        )

        response = _post(harness, event)

        assert response.status_code == 200
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]
        # Refused before any write — no UPDATE was issued.
        assert not any("UPDATE patient_charges" in sql for sql, _ in harness["conn"].statements)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


def _update_call(conn: _FakeConn) -> tuple[str, dict[str, Any]]:
    for sql, params in conn.statements:
        if "UPDATE patient_charges" in sql:
            assert params is not None
            return sql, params
    raise AssertionError(f"no ledger UPDATE issued; saw {[s for s, _ in conn.statements]}")


class TestReconciliation:
    def test_a_success_event_marks_the_row_succeeded(self, harness: dict[str, Any]) -> None:
        response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 200
        _, params = _update_call(harness["conn"])
        assert params["new_status"] == "succeeded"
        assert params["pi"] == "pi_ours"
        # Only a still-open row may be flipped — a refund is never un-refunded.
        assert params["allowed"] == ["pending", "failed"]

        # The practice schema is bound and the row policy armed before the write.
        seen = [sql for sql, _ in harness["conn"].statements]
        assert any("search_path" in sql for sql in seen)
        assert any(_SCHEMA in sql for sql in seen)
        assert any("app.current_user_id" in sql for sql in seen)

    def test_a_failure_event_records_the_decline_code(self, harness: dict[str, Any]) -> None:
        event = _event(
            "payment_intent.payment_failed",
            _ours(
                {
                    "id": "pi_ours",
                    "last_payment_error": {
                        "code": "card_declined",
                        "decline_code": "insufficient_funds",
                    },
                }
            ),
        )

        response = _post(harness, event)

        assert response.status_code == 200
        _, params = _update_call(harness["conn"])
        assert params["new_status"] == "failed"
        assert params["status_detail"] == "insufficient_funds"
        # A failure may only overwrite a pending row — never un-succeed one.
        assert params["allowed"] == ["pending"]

    def test_a_refund_event_flips_the_row_to_refunded(self, harness: dict[str, Any]) -> None:
        """A refund issued in the practice's own dashboard is the ONLY way the
        ledger learns about one."""
        harness["conn"].current_status = "succeeded"
        event = _event("charge.refunded", _ours({"id": "ch_1", "payment_intent": "pi_ours"}))

        response = _post(harness, event)

        assert response.status_code == 200
        _, params = _update_call(harness["conn"])
        assert params["new_status"] == "refunded"
        assert params["pi"] == "pi_ours"
        assert params["status_detail"] is None

    def test_an_unhandled_event_type_is_acknowledged_without_touching_a_practice(
        self, harness: dict[str, Any]
    ) -> None:
        response = _post(harness, _event("payment_intent.created", {"id": "pi_1"}))
        assert response.status_code == 200
        assert harness["conn"].statements == []

    def test_an_unknown_practice_is_acknowledged(self, harness: dict[str, Any]) -> None:
        harness["session"]._practice = None

        response = _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        assert response.status_code == 200
        assert harness["conn"].statements == []
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]

    def test_a_refund_of_a_charge_with_no_payment_intent_is_recorded(
        self, harness: dict[str, Any]
    ) -> None:
        """This application only ever charges through a PaymentIntent, so a
        charge with none can never match a ledger row. Nothing a redelivery
        could fix, so this one IS recorded."""
        response = _post(
            harness, _event("charge.refunded", {"id": "ch_legacy", "payment_intent": None})
        )

        assert response.status_code == 200
        assert harness["conn"].statements == []
        assert [row.event_id for row in harness["session"].added] == ["evt_1"]


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


class TestPracticeResolution:
    def test_a_single_practice_deployment_falls_back_to_the_default_schema(
        self, harness: dict[str, Any]
    ) -> None:
        """A deployment running one practice has no registry to key on and
        stamps no practice id, so its events resolve to the default schema."""
        harness["settings"].multi_tenancy_enabled = False
        event = _event(
            "payment_intent.succeeded",
            {
                "id": "pi_ours",
                "metadata": {"pablo_charge_id": _CHARGE_ID, "pablo_user_id": _USER_ID},
            },
        )

        response = _post(harness, event)

        assert response.status_code == 200
        seen = [sql for sql, _ in harness["conn"].statements]
        assert any("search_path = practice," in sql for sql in seen)
        # Nothing named a practice, so the dedupe row names none either.
        assert harness["session"].added[0].practice_id is None

    def test_a_multi_practice_deployment_refuses_to_guess(self, harness: dict[str, Any]) -> None:
        """With several practices in one deployment, an event that names none
        cannot be placed. Acknowledge it rather than picking a schema."""
        event = _event(
            "payment_intent.succeeded",
            {
                "id": "pi_ours",
                "metadata": {"pablo_charge_id": _CHARGE_ID, "pablo_user_id": _USER_ID},
            },
        )

        response = _post(harness, event)

        assert response.status_code == 200
        assert harness["conn"].statements == []


class TestProcessedEventRow:
    def test_the_processor_event_time_is_recorded(self, harness: dict[str, Any]) -> None:
        _post(harness, _event("payment_intent.succeeded", _ours({"id": "pi_ours"})))

        row = harness["session"].added[0]
        assert row.event_type == "payment_intent.succeeded"
        assert row.event_created_at == datetime.fromtimestamp(1_760_000_000, tz=UTC)
        assert row.processed_at is not None
