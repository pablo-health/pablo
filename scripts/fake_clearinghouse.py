# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in clearinghouse for the end-to-end stack.

Serves the endpoint paths the clearinghouse adapter (``app.claims.stedi``)
calls, answering each from the responses recorded in
``backend/tests/fixtures/clearinghouse/``, and plays the asynchronous half of
the protocol the vendor would: after a claim is accepted, a 277CA arrives on
one timer and an 835 on a second, each announced to the backend as a signed
"transaction processed" webhook and readable afterwards from the transaction
endpoints. Nothing here reaches the network except that webhook.

Rules, all keyed on the claim's ``patientControlNumber``:

* ``REJ-DX…``   → the recorded diagnosis-specificity edit rejection (400)
* ``REJ-PTR…``  → the recorded diagnosis-pointer edit rejection (400)
* ``REJ-SUB…``  → the recorded subscriber-demographics edit rejection (400)
* anything else → the recorded accept, then the 277CA and the 835 on their
  timers, with the claim's own control number, line numbers and amounts
  substituted so the remittance reads as paid in full for what was charged

A submission's ``Idempotency-Key`` header is echoed on the response and a
retry with the same key gets the same answer without starting new timers.
Unknown request fields (a ``dependent``, say) are ignored, as the vendor
would parse past them.

Test hooks live under ``/_fake``: ``GET /_fake/received`` lists every request
and webhook delivery since the last reset, ``POST /_fake/reset`` clears that
log and cancels pending timers, ``POST /_fake/deliver`` fires a 277CA or 835
for a control number immediately.

Configuration is by environment: ``FAKE_CLEARINGHOUSE_FIXTURES`` (directory
of recordings), ``FAKE_CLEARINGHOUSE_WEBHOOK_URL`` and
``FAKE_CLEARINGHOUSE_WEBHOOK_SECRET`` (where and how to sign deliveries),
``FAKE_CLEARINGHOUSE_PUBLIC_URL`` (the base written into artifact URLs),
``FAKE_CLEARINGHOUSE_277_DELAY_SECONDS`` / ``FAKE_CLEARINGHOUSE_835_DELAY_SECONDS``.

Webhook signing follows the Standard Webhooks scheme the vendor uses:
``webhook-id``, ``webhook-timestamp`` and ``webhook-signature: v1,<base64>``
headers, HMAC-SHA256 over ``"<id>.<timestamp>.<body>"``. A secret prefixed
``whsec_`` is base64-decoded first, as the scheme specifies; any other value
is used as raw bytes.

Run locally with ``uvicorn scripts.fake_clearinghouse:app --port 8080``; the
compose stack builds it from ``scripts/e2e/fake-clearinghouse.Dockerfile``.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hmac
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("fake_clearinghouse")

_REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(
    os.environ.get(
        "FAKE_CLEARINGHOUSE_FIXTURES",
        str(_REPO_ROOT / "backend" / "tests" / "fixtures" / "clearinghouse"),
    )
)
PUBLIC_URL = os.environ.get("FAKE_CLEARINGHOUSE_PUBLIC_URL", "http://localhost:8080").rstrip("/")
WEBHOOK_URL = os.environ.get("FAKE_CLEARINGHOUSE_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("FAKE_CLEARINGHOUSE_WEBHOOK_SECRET", "")
DELAY_277_SECONDS = float(os.environ.get("FAKE_CLEARINGHOUSE_277_DELAY_SECONDS", "2"))
DELAY_835_SECONDS = float(os.environ.get("FAKE_CLEARINGHOUSE_835_DELAY_SECONDS", "5"))

# Vendor API version prefixes, one per host the adapter talks to. All are
# served from this one process.
HEALTHCARE = "/2024-04-01"
PAYERS = "/2024-04-01"
CORE = "/2023-08-01"
ENROLLMENTS = "/2024-09-01"

#: Control-number prefix → the recorded 400 edit rejection it earns.
REJECTIONS: dict[str, str] = {
    "REJ-DX": "837p_submission_edit_rejected_dx_specificity.json",
    "REJ-PTR": "837p_submission_edit_rejected_dx_pointer.json",
    "REJ-SUB": "837p_submission_edit_rejected_subscriber_demographics.json",
}

#: Values the recordings carry for the one claim they were captured from.
#: Substituted everywhere they appear so a document refers to the claim under
#: test rather than the recorded one.
_RECORDED_CONTROL_NUMBER = "88659891"
_RECORDED_LINE_CONTROL_NUMBER = "886598911"
_RECORDED_CORRELATION_ID = "01M1T7001FRW15MVE0SSW4FA7G"

_NAMESPACE = uuid.UUID("7f1c2a8e-0e5b-4d4a-9a9b-3c1f5e2d6b70")

TransactionKind = Literal["277", "835"]


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _deep_replace(value: Any, replacements: dict[str, str]) -> Any:
    """Return ``value`` with every string equal to a key swapped for its replacement."""
    if isinstance(value, dict):
        return {k: _deep_replace(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_replace(v, replacements) for v in value]
    if isinstance(value, str) and value in replacements:
        return replacements[value]
    return value


def _correlation_id(control_number: str) -> str:
    """A stable, ULID-shaped vendor claim id for a control number."""
    digest = uuid.uuid5(_NAMESPACE, f"claim:{control_number}").hex.upper()
    return f"01E2E{digest[:21]}"


def _transaction_id(kind: TransactionKind, control_number: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{control_number}"))


def _event_id(kind: TransactionKind, control_number: str) -> str:
    return "evt_" + uuid.uuid5(_NAMESPACE, f"event:{kind}:{control_number}").hex


class _State:
    """Everything a test can observe or reset."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        #: transaction id → {"document": ..., "report": ...}
        self.transactions: dict[str, dict[str, Any]] = {}
        #: control number → the accepted submission request body
        self.claims: dict[str, dict[str, Any]] = {}
        #: Idempotency-Key → (status, body) of the submission it first produced
        self.replays: dict[str, tuple[int, dict[str, Any]]] = {}
        self.timers: set[asyncio.Task[None]] = set()

    def reset(self) -> None:
        for task in self.timers:
            task.cancel()
        self.timers.clear()
        self.requests.clear()
        self.webhooks.clear()
        self.transactions.clear()
        self.claims.clear()
        self.replays.clear()


state = _State()
app = FastAPI(title="fake clearinghouse", docs_url=None, redoc_url=None)


async def _record(request: Request, control_number: str | None = None) -> Any:
    """Log a request the way a test wants to see it and return its JSON body."""
    raw = await request.body()
    body: Any = None
    if raw:
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode("utf-8", errors="replace")
    headers = {
        k.lower(): ("[redacted]" if k.lower() == "authorization" else v)
        for k, v in request.headers.items()
    }
    state.requests.append(
        {
            "at": _now(),
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": headers,
            "json": body,
            "control_number": control_number,
        }
    )
    return body


def _vendor_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)


# --- payers, eligibility ---------------------------------------------------


@app.get(f"{PAYERS}/payers/search")
async def search_payers(request: Request) -> Any:
    await _record(request)
    return _load("payer_search_test_payer.json")


@app.post(f"{HEALTHCARE}/change/medicalnetwork/eligibility/v3")
async def check_eligibility(request: Request) -> Any:
    body = await _record(request)
    if not isinstance(body, dict) or not body.get("subscriber", {}).get("memberId"):
        return _vendor_error(400, "INVALID_REQUEST_BODY", "subscriber.memberId is required")
    return _load("eligibility_271_active.json")


# --- claim submission ------------------------------------------------------


def _line_control_numbers(claim: dict[str, Any], control_number: str) -> list[str]:
    lines = claim.get("claimInformation", {}).get("serviceLines", [])
    numbers = [str(line.get("providerControlNumber") or "") for line in lines]
    return [n or f"{control_number}{i + 1}" for i, n in enumerate(numbers)] or [
        f"{control_number}1"
    ]


def _substitute_submission(result: dict[str, Any], claim: dict[str, Any], control: str) -> None:
    ref = result.get("claimReference")
    if not ref:
        return
    correlation = _correlation_id(control)
    ref["patientControlNumber"] = control
    ref["correlationId"] = correlation
    ref["rhclaimNumber"] = correlation
    ref["timeOfResponse"] = _now()
    ref["serviceLines"] = [
        {"lineItemControlNumber": n} for n in _line_control_numbers(claim, control)
    ]


@app.post(f"{HEALTHCARE}/change/medicalnetwork/professionalclaims/v3/submission")
async def submit_claim(request: Request) -> Any:
    raw = await request.body()
    try:
        claim = json.loads(raw) if raw else None
    except ValueError:
        claim = None
    control = ""
    if isinstance(claim, dict):
        control = str(claim.get("claimInformation", {}).get("patientControlNumber") or "")
    await _record(request, control_number=control or None)

    if not isinstance(claim, dict) or not control:
        return _vendor_error(
            400, "INVALID_REQUEST_BODY", "claimInformation.patientControlNumber is required"
        )

    # A keyed retry gets the answer the first attempt got and starts no new
    # timers; the key comes back on the response so a caller can see it held.
    key = request.headers.get("idempotency-key", "")
    echo = {"idempotency-key": key} if key else {}
    if key and key in state.replays:
        status_code, body = state.replays[key]
        return JSONResponse(body, status_code=status_code, headers=echo)

    rejection = next((f for p, f in REJECTIONS.items() if control.startswith(p)), None)
    result = _load(rejection or "837p_submission_success_test_payer.json")
    status_code = 400 if rejection else 200
    _substitute_submission(result, claim, control)

    if not rejection:
        state.claims[control] = claim
        _schedule(control, "277", DELAY_277_SECONDS)
        _schedule(control, "835", DELAY_835_SECONDS)
    if key:
        state.replays[key] = (status_code, result)
    return JSONResponse(result, status_code=status_code, headers=echo)


# --- the asynchronous half: 277CA, 835, webhooks ----------------------------


def _schedule(control: str, kind: TransactionKind, delay: float) -> None:
    task = asyncio.create_task(_deliver_after(control, kind, delay))
    state.timers.add(task)
    task.add_done_callback(state.timers.discard)


async def _deliver_after(control: str, kind: TransactionKind, delay: float) -> None:
    await asyncio.sleep(delay)
    await _deliver(control, kind)


def _polling_template(kind: TransactionKind) -> dict[str, Any]:
    items = _load("polling_transactions_277_and_835.json")["items"]
    wanted = {"277": "277", "835": "835"}[kind]
    for item in items:
        if (
            item.get("direction") == "INBOUND"
            and item["x12"]["metadata"]["transaction"]["transactionSetIdentifier"] == wanted
        ):
            return copy.deepcopy(item)
    msg = f"polling fixture has no inbound {wanted}"
    raise RuntimeError(msg)


def _build_transaction(control: str, kind: TransactionKind) -> dict[str, Any]:
    """The transaction document the polling endpoint would list for this claim."""
    claim = state.claims.get(control, {})
    transaction_id = _transaction_id(kind, control)
    lines = _line_control_numbers(claim, control)
    doc: dict[str, Any] = _deep_replace(
        _polling_template(kind),
        {
            _RECORDED_CONTROL_NUMBER: control,
            _RECORDED_LINE_CONTROL_NUMBER: lines[0],
            _RECORDED_CORRELATION_ID: _correlation_id(control),
        },
    )
    doc["transactionId"] = transaction_id
    doc["processedAt"] = _now()
    for artifact in doc.get("artifacts", []):
        artifact["url"] = f"{PUBLIC_URL}{CORE}/transactions/{transaction_id}/{artifact['usage']}"
    return doc


def _build_277_report(control: str) -> dict[str, Any]:
    """The 277CA as JSON: the recorded acknowledgement for this claim."""
    claim = state.claims.get(control, {})
    report: dict[str, Any] = _load("837p_submission_success_test_payer.json")
    _substitute_submission(report, claim, control)
    report["transactionId"] = _transaction_id("277", control)
    return report


def _build_835_report(control: str) -> dict[str, Any]:
    """The 835 as JSON, paying the claim in full for what it charged."""
    claim = state.claims.get(control, {})
    lines = _line_control_numbers(claim, control)
    report: dict[str, Any] = _deep_replace(
        _load("835_report_paid_in_full.json"),
        {
            _RECORDED_CONTROL_NUMBER: control,
            _RECORDED_LINE_CONTROL_NUMBER: lines[0],
            _RECORDED_CORRELATION_ID: _correlation_id(control),
        },
    )
    report["meta"]["transactionId"] = _transaction_id("835", control)

    info = claim.get("claimInformation", {})
    charge = str(info.get("claimChargeAmount") or "")
    service_lines = info.get("serviceLines", [])
    for transaction in report.get("transactions", []):
        if charge:
            transaction["financialInformation"]["totalActualProviderPaymentAmount"] = charge
        for detail in transaction.get("detailInfo", []):
            for payment in detail.get("paymentInfo", []):
                if charge:
                    payment["claimPaymentInfo"]["claimPaymentAmount"] = charge
                    payment["claimPaymentInfo"]["totalClaimChargeAmount"] = charge
                if service_lines:
                    payment["serviceLines"] = [
                        _paid_line(payment["serviceLines"][0], line, number)
                        for line, number in zip(service_lines, lines, strict=False)
                    ]
    return report


def _paid_line(template: dict[str, Any], line: dict[str, Any], number: str) -> dict[str, Any]:
    paid = copy.deepcopy(template)
    service = line.get("professionalService", {})
    amount = str(service.get("lineItemChargeAmount") or "")
    paid["lineItemControlNumber"] = number
    if line.get("serviceDate"):
        paid["serviceDate"] = str(line["serviceDate"])
    payment = paid["servicePaymentInformation"]
    if service.get("procedureCode"):
        payment["adjudicatedProcedureCode"] = service["procedureCode"]
        payment["submittedAdjudicatedProcedureCode"] = service["procedureCode"]
    if "procedureModifiers" in service:
        payment["adjudicatedProcedureModifierCodes"] = list(service["procedureModifiers"])
        payment["submittedAdjudicatedProcedureModifierCodes"] = list(service["procedureModifiers"])
    if amount:
        payment["lineItemChargeAmount"] = amount
        payment["lineItemProviderPaymentAmount"] = amount
    if service.get("serviceUnitCount"):
        payment["unitsOfServicePaidCount"] = str(service["serviceUnitCount"])
    return paid


def _signing_key() -> bytes:
    if WEBHOOK_SECRET.startswith("whsec_"):
        return base64.b64decode(WEBHOOK_SECRET.removeprefix("whsec_"))
    return WEBHOOK_SECRET.encode()


def _sign(event_id: str, timestamp: int, body: bytes) -> str:
    signed = f"{event_id}.{timestamp}.".encode() + body
    digest = hmac.new(_signing_key(), signed, "sha256").digest()
    return "v1," + base64.b64encode(digest).decode()


async def _deliver(control: str, kind: TransactionKind) -> dict[str, Any]:
    """Publish the transaction and post its webhook; returns the delivery record."""
    document = _build_transaction(control, kind)
    report = _build_277_report(control) if kind == "277" else _build_835_report(control)
    transaction_id = document["transactionId"]
    state.transactions[transaction_id] = {"document": document, "report": report}

    event_id = _event_id(kind, control)
    event = {
        "id": event_id,
        "source": "stedi.core",
        "detail-type": "transaction.processed",
        "time": _now(),
        "detail": document,
    }
    body = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    delivery: dict[str, Any] = {
        "at": _now(),
        "kind": kind,
        "control_number": control,
        "transaction_id": transaction_id,
        "event_id": event_id,
        "url": WEBHOOK_URL,
        "status": None,
        "error": None,
    }
    if WEBHOOK_URL:
        headers = {
            "content-type": "application/json",
            "webhook-id": event_id,
            "webhook-timestamp": str(timestamp),
            "webhook-signature": _sign(event_id, timestamp, body),
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(WEBHOOK_URL, content=body, headers=headers)
            delivery["status"] = response.status_code
        except httpx.HTTPError as exc:
            delivery["error"] = str(exc)
            logger.warning("webhook delivery failed kind=%s control=%s: %s", kind, control, exc)
    state.webhooks.append(delivery)
    return delivery


# --- transactions (polling + reports) ---------------------------------------


@app.get(f"{CORE}/transactions")
async def list_transactions(request: Request) -> Any:
    await _record(request)
    return {
        "items": [entry["document"] for entry in state.transactions.values()],
        "nextPageToken": None,
    }


@app.get(f"{CORE}/transactions/{{transaction_id}}")
async def get_transaction(transaction_id: str, request: Request) -> Any:
    await _record(request)
    entry = state.transactions.get(transaction_id)
    if entry is None:
        return _vendor_error(404, "NOT_FOUND", f"transaction {transaction_id} not found")
    return entry["document"]


@app.get(f"{CORE}/transactions/{{transaction_id}}/{{usage}}")
async def get_transaction_report(transaction_id: str, usage: str, request: Request) -> Any:
    """The transaction's artifact: the JSON report for an inbound document."""
    await _record(request)
    entry = state.transactions.get(transaction_id)
    if entry is None:
        return _vendor_error(404, "NOT_FOUND", f"transaction {transaction_id} not found")
    if usage not in ("input", "output"):
        return _vendor_error(404, "NOT_FOUND", f"no {usage} artifact")
    return entry["report"]


# --- enrollments -----------------------------------------------------------


@app.post(f"{ENROLLMENTS}/providers")
async def create_provider(request: Request) -> Any:
    body = await _record(request)
    record = _load("enrollment_create_provider.json")
    if isinstance(body, dict):
        for field in ("name", "npi", "taxId", "taxIdType", "contacts"):
            if body.get(field) is not None:
                record[field] = body[field]
    return record


@app.post(f"{ENROLLMENTS}/enrollments")
async def create_enrollment(request: Request) -> Any:
    await _record(request)
    return _load("enrollment_create_enrollment_835.json")


@app.get(f"{ENROLLMENTS}/enrollments")
async def list_enrollments(request: Request) -> Any:
    await _record(request)
    return {"items": [_load("enrollment_create_enrollment_835.json")], "nextPageToken": None}


# --- test hooks ------------------------------------------------------------


@app.get("/_fake/received")
async def received() -> Any:
    return {
        "requests": state.requests,
        "webhooks": state.webhooks,
        "transactions": [entry["document"] for entry in state.transactions.values()],
    }


@app.post("/_fake/reset")
async def reset() -> Any:
    state.reset()
    return {"ok": True}


@app.post("/_fake/deliver")
async def deliver(request: Request) -> Any:
    """Fire the 277CA or 835 for a control number now instead of on its timer."""
    body = await request.json()
    control = str(body.get("control_number") or "")
    kind = str(body.get("kind") or "")
    if not control or kind not in ("277", "835"):
        return JSONResponse(
            {"error": "control_number and kind (277 or 835) are required"}, status_code=400
        )
    return await _deliver(control, "277" if kind == "277" else "835")
