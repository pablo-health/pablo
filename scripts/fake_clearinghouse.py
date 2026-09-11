# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in clearinghouse for the end-to-end stack.

Serves the endpoint paths the clearinghouse adapter (``app.claims.stedi``)
calls, answering each from the responses recorded in
``backend/tests/fixtures/clearinghouse/``, and plays the asynchronous half of
the protocol the vendor would: after a claim is accepted, a 277CA arrives on
one timer and an 835 on a second, each announced to the backend as a signed
"transaction processed" webhook and readable afterwards from the transaction
endpoints. Nothing here reaches the network except that webhook.

Two claim submission endpoints are served, because the adapter is moving
from one to the other one operation at a time. The compatibility shim
(``POST {HEALTHCARE}/change/medicalnetwork/professionalclaims/v3/submission``)
answers with the legacy envelope; the vendor's own claim API
(``POST {CLAIMS}/professional-claim-submissions``), which is what the SDK
adapter now calls, answers with ``{claimId, submissionId}`` or, for a
rejection, ``{claimId, submissionId, errors: [{description}]}``. Both apply
the same rules, keyed on the claim's ``patientControlNumber``:

* ``REJ-DX…``   → the recorded diagnosis-specificity edit rejection (400)
* ``REJ-PTR…``  → the recorded diagnosis-pointer edit rejection (400)
* ``REJ-SUB…``  → the recorded subscriber-demographics edit rejection (400)
* ``PART-…``    → accepted, then a 277CA and an 835 that pays part of each
  line and assigns the rest to the client as patient responsibility (a CAS
  adjustment, group ``PR``), with the claim total agreeing with what the
  lines carry
* ``DENY-…``    → accepted, then a 277CA and an 835 that denies the claim
  and assigns the whole charge to the client
* ``NOSUM-…``   → accepted, then a 277CA and an 835 that CONTRADICTS ITSELF:
  the claim states a patient-responsibility total and the service lines
  itemise the same money as a contractual write-off, so ``CLP05`` and the
  ``PR`` adjustments disagree. Everything else about the document balances,
  so the engine's other checks pass and exactly one of them fires
* anything else → the recorded accept, then the 277CA and the 835 on their
  timers, with the claim's own control number, line numbers and amounts
  substituted so the remittance reads as paid in full for what was charged

Every 835 rule starts from the recorded paid-in-full remittance and edits its
amounts rather than building a new document, so a ``PART-`` or ``DENY-``
claim gets the same shape the accept path already produces.

Which rule applies is decided in ONE place, :func:`_outcome_for`, in this
order: a per-claim override, then the control number's prefix, then a
default armed for the whole run, then paid-in-full.

The overrides exist because a browser test cannot reach the prefixes at all:
a claim filed through the app gets a server-generated control number. It
arms ``POST /_fake/outcome`` with no control number BEFORE filing — the 835
follows five seconds after submission, and a test that waited to learn the
number would be racing that timer. Overrides are recorded on the state
rather than passed down a call, so a timer-fired 835 and one forced through
``/_fake/deliver`` can never say different things about the same claim.

A prefix beats the armed default, so a spec that armed one outcome and then
deliberately filed a ``DENY-`` claim gets the denial it asked for.

A WARNING about the ``NOSUM-`` rule, because there is a way to produce the
same symptom by accident and the two must not be confused. The sibling fake
used by unit tests (``tests/claims_pipeline_fakes.py`` ``remittance_report``)
overrides claim-level amounts but not line-level ones, so any caller passing
a paid amount other than the recorded one gets a self-inconsistent remittance
without meaning to. The disagreement here is deliberate, is built by moving
one adjustment's group code, and is written so a reader can see that it was
on purpose.

A submission's ``Idempotency-Key`` header is echoed on the response and a
retry with the same key gets the same answer without starting new timers; the
same key against a changed request body is refused, as the vendor refuses it.
Unknown request fields (a ``dependent``, say) are ignored, as the vendor
would parse past them.

The vendor's claim-lifecycle API is served too
(``GET {CLAIMS}/claims/{id}/timeline``), answering from the recording of a
real claim's whole life and trimmed to what has actually happened to the
claim being asked about.

Test hooks live under ``/_fake``: ``GET /_fake/received`` lists every request
and webhook delivery since the last reset, ``POST /_fake/reset`` clears that
log and cancels pending timers, ``POST /_fake/deliver`` fires a 277CA or 835
for a control number immediately.

Configuration is by environment: ``FAKE_CLEARINGHOUSE_FIXTURES`` (directory
of recordings), ``FAKE_CLEARINGHOUSE_WEBHOOK_URL`` and
``FAKE_CLEARINGHOUSE_WEBHOOK_SECRET`` (where and how to sign deliveries),
``FAKE_CLEARINGHOUSE_PUBLIC_URL`` (the base written into artifact URLs),
``FAKE_CLEARINGHOUSE_BROWSER_URL`` (the base for links a browser follows),
``FAKE_CLEARINGHOUSE_277_DELAY_SECONDS`` / ``FAKE_CLEARINGHOUSE_835_DELAY_SECONDS``.

A delivered event carries the vendor's own event envelope — ``id``,
``type``, and a ``resource`` naming the transaction — and NOT the transaction
document itself. That is the vendor's design: the event is a pointer, and the
reader fetches the transaction from the endpoints above. See
``webhook_transaction_processed.json`` for the shape.

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
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
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
#: Where a *browser* reaches this app. The vendor's upload URL is followed
#: by our server and its download URL by the practice's browser, and in a
#: container stack those are two different hostnames for the same port.
#: Defaults to ``PUBLIC_URL`` so a single-host run needs no second setting.
BROWSER_URL = os.environ.get("FAKE_CLEARINGHOUSE_BROWSER_URL", PUBLIC_URL).rstrip("/")
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
CLAIMS = "/2025-03-07"

#: Control-number prefix → the recorded 400 edit rejection it earns.
REJECTIONS: dict[str, str] = {
    "REJ-DX": "837p_submission_edit_rejected_dx_specificity.json",
    "REJ-PTR": "837p_submission_edit_rejected_dx_pointer.json",
    "REJ-SUB": "837p_submission_edit_rejected_subscriber_demographics.json",
}

#: Control-number prefixes that change what the 835 says rather than what
#: the submission answers: both are accepted at submission time and only
#: diverge from paid-in-full once the remittance is built.
PARTIAL_PREFIX = "PART-"
DENIAL_PREFIX = "DENY-"
DISAGREEMENT_PREFIX = "NOSUM-"

#: What an 835 says about a claim. Named once so the prefix rules, the
#: ``/_fake/deliver`` override and the document builder cannot drift apart.
Outcome = Literal["paid", "partial", "denied", "disagreeing"]

OUTCOMES: tuple[str, ...] = ("paid", "partial", "denied", "disagreeing")

#: Prefix → outcome, longest-lived mechanism first. Order is irrelevant:
#: the prefixes do not overlap.
_PREFIX_OUTCOMES: dict[str, Outcome] = {
    DENIAL_PREFIX: "denied",
    PARTIAL_PREFIX: "partial",
    DISAGREEMENT_PREFIX: "disagreeing",
}

#: What a ``PART-`` claim pays of each line; the rest becomes the client's.
_PARTIAL_PAID_FRACTION = Decimal("0.6")

#: X12 CLP02 for a denied claim, and the CAS (``PR``) reason codes the fake
#: writes for the client's share: a deductible on a partial payment, a plan
#: exclusion on a denial.
_DENIED_CLAIM_STATUS_CODE = "4"
_PATIENT_RESPONSIBILITY_GROUP = "PR"

#: The group a ``NOSUM-`` claim itemises the client's share under instead.
#: ``CO`` is a contractual write-off — money nobody owes — so a claim that
#: states a patient-responsibility total and itemises it as ``CO`` has said
#: two different things about the same money. That is the whole trick, and
#: it is one constant rather than a second document builder.
_CONTRACTUAL_GROUP = "CO"
_CO_REASON_FEE_SCHEDULE = "45"
_PR_REASON_DEDUCTIBLE = "1"
_PR_REASON_NOT_COVERED = "96"

#: Values the recordings carry for the one claim they were captured from.
#: Substituted everywhere they appear so a document refers to the claim under
#: test rather than the recorded one.
_RECORDED_CONTROL_NUMBER = "88659891"
_RECORDED_LINE_CONTROL_NUMBER = "886598911"
_RECORDED_CORRELATION_ID = "01M1T7001FRW15MVE0SSW4FA7G"
#: The 277CA was captured from a DIFFERENT live claim than the 835, so it
#: carries its own trace number to substitute.
_RECORDED_277_TRACE_NUMBER = "LIVE50D1D98E2364"

_NAMESPACE = uuid.UUID("7f1c2a8e-0e5b-4d4a-9a9b-3c1f5e2d6b70")

#: The account a webhook event says it came from. Synthetic, and matched
#: to the recorded event fixture so the two read as one account.
_ACCOUNT_ID = "11111111-2222-3333-4444-555555555555"

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


def _control_for_claim_id(claim_id: str) -> str | None:
    """The control number a vendor claim id belongs to, if this app minted it."""
    return next(
        (control for control in state.claims if _correlation_id(control) == claim_id),
        None,
    )


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
        #: Idempotency-Key → (the request body it was first used with, the
        #: native response that body earned)
        self.native_replays: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        self.timers: set[asyncio.Task[None]] = set()
        #: The one enrollment task the harness offers, and its documents.
        self.task_complete: bool = False
        self.task_values: list[dict[str, Any]] = []
        #: document id → the document as the enrollment reports it
        self.documents: dict[str, dict[str, Any]] = {}
        #: control number → the outcome a test forced, overriding the prefix.
        #: Recorded rather than passed through, so a timer-fired 835 and one
        #: forced through ``/_fake/deliver`` cannot disagree about the same
        #: claim.
        self.outcomes: dict[str, str] = {}
        #: The outcome every claim gets unless its own prefix or an explicit
        #: per-claim override says otherwise. Armed BEFORE filing, because a
        #: claim filed through the app gets its control number from the
        #: server and the 835 follows five seconds later — a test that waited
        #: to learn the number would be racing the timer.
        self.default_outcome: str | None = None

    def reset(self) -> None:
        for task in self.timers:
            task.cancel()
        self.timers.clear()
        self.requests.clear()
        self.webhooks.clear()
        self.transactions.clear()
        self.claims.clear()
        self.replays.clear()
        self.task_complete = False
        self.task_values.clear()
        self.documents.clear()
        self.native_replays.clear()
        self.outcomes.clear()
        self.default_outcome = None


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


def _native_service_lines_as_legacy(claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The native body's service lines, shaped the way the report builders
    below already read a submitted claim's lines — so those builders need no
    changes to serve a claim filed through either endpoint."""
    lines = []
    for line in claim.get("serviceLines", []):
        procedure = line.get("procedureCode") or {}
        lines.append(
            {
                "providerControlNumber": line.get("lineItemControlNumber"),
                "serviceDate": (line.get("datesOfService") or {}).get("start"),
                "professionalService": {
                    "procedureCode": procedure.get("code"),
                    "procedureModifiers": procedure.get("modifiers") or [],
                    "lineItemChargeAmount": line.get("lineItemChargeAmount"),
                    "serviceUnitCount": line.get("units"),
                },
            }
        )
    return lines


def _legacy_shape_for_reports(claim: dict[str, Any]) -> dict[str, Any]:
    """A native submission's charge and lines, in the legacy claim shape the
    277CA/835 builders expect from ``state.claims``."""
    billing = claim.get("billing") or {}
    return {
        "claimInformation": {
            "claimChargeAmount": billing.get("totalCharge"),
            "serviceLines": _native_service_lines_as_legacy(claim),
        }
    }


@app.post(f"{CLAIMS}/professional-claim-submissions")
async def submit_claim_native(request: Request) -> Any:
    """The vendor's own claim API, which the SDK adapter files claims to.

    Answers with the native shape (``claimId``/``submissionId``, and an
    ``errors`` list for a rejection) rather than the compatibility shim's
    envelope above. The claim id is stable for a control number across
    resubmissions — the property the claim-lifecycle API keys everything
    else on — while the submission id is fresh on every attempt, as the
    vendor mints it.
    """
    raw = await request.body()
    try:
        claim = json.loads(raw) if raw else None
    except ValueError:
        claim = None
    control = ""
    if isinstance(claim, dict):
        control = str((claim.get("billing") or {}).get("patientControlNumber") or "")
    await _record(request, control_number=control or None)

    if not isinstance(claim, dict) or not control:
        return _vendor_error(
            400, "InvalidRequestException", "billing.patientControlNumber is required"
        )

    # A keyed retry of the same body gets the answer the first attempt got
    # and starts no new timers; the same key against a different body is
    # refused exactly as the vendor refuses it — a client error naming the
    # reused key, not a stored claim.
    key = request.headers.get("idempotency-key", "")
    if key:
        replayed = state.native_replays.get(key)
        if replayed is not None:
            first_claim, first_result = replayed
            if claim != first_claim:
                return _vendor_error(
                    400,
                    "InvalidRequestException",
                    "the idempotency-key was previously used with a different request",
                )
            return JSONResponse(first_result)

    claim_id = _correlation_id(control)
    rejection = next((f for p, f in REJECTIONS.items() if control.startswith(p)), None)
    result: dict[str, Any] = {"claimId": claim_id, "submissionId": str(uuid.uuid4())}
    if rejection is None:
        state.claims[control] = _legacy_shape_for_reports(claim)
        _schedule(control, "277", DELAY_277_SECONDS)
        _schedule(control, "835", DELAY_835_SECONDS)
    else:
        result["errors"] = [
            {"description": error["description"]} for error in _load(rejection)["errors"]
        ]

    if key:
        state.native_replays[key] = (claim, result)
    return JSONResponse(result)


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
    """The 277CA as JSON, from the recorded acknowledgement.

    This used to load ``837p_submission_success_test_payer.json`` — the
    SYNCHRONOUS submission accept, which is a different document entirely
    (``claimReference``/``status``, where a 277CA report carries
    ``meta``/``transactions``). ``parse_277`` found no transactions in it and
    returned zero acknowledgements, so every acknowledgement the harness
    delivered was reported as naming a claim nobody owned. PABLO-1qox.
    """
    report: dict[str, Any] = _deep_replace(
        _load("277ca_report_clearinghouse_forwarded.json"),
        {_RECORDED_277_TRACE_NUMBER: control},
    )
    report["meta"]["transactionId"] = _transaction_id("277", control)
    return report


def _outcome_for(control: str) -> Outcome:
    """What this claim's 835 says. The single decision, read by everything.

    A forced outcome wins over the prefix so a browser test can reach these
    rules at all: a claim filed through the app gets a server-generated
    control number and cannot be given one.
    """
    forced = state.outcomes.get(control)
    if forced in OUTCOMES:
        return forced  # type: ignore[return-value]
    for prefix, outcome in _PREFIX_OUTCOMES.items():
        if control.startswith(prefix):
            return outcome
    # A prefix beats the default: a spec that armed one outcome for the run
    # and then deliberately filed a ``DENY-`` claim means the ``DENY-``.
    if state.default_outcome in OUTCOMES:
        return state.default_outcome  # type: ignore[return-value]
    return "paid"


def _line_split(charge: Decimal, outcome: Outcome) -> tuple[Decimal, Decimal]:
    """What one line pays, and what is left over for an adjustment to explain.

    The second figure is NOT "what the client owes" — which group code it is
    written under is the caller's business, and on a ``disagreeing`` claim it
    is deliberately written under two different ones.
    """
    if outcome == "denied":
        return Decimal("0.00"), charge
    if outcome in ("partial", "disagreeing"):
        paid = (charge * _PARTIAL_PAID_FRACTION).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return paid, charge - paid
    return charge, Decimal("0.00")


def _build_835_report(control: str) -> dict[str, Any]:
    """The 835 as JSON: paid in full, paid in part, or denied, by the claim's own numbers.

    The claim-level totals are summed from what the lines actually carry
    rather than independently re-derived from the charge, so CLP05 always
    agrees with the itemisation the parser checks it against.
    """
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
    charge = info.get("claimChargeAmount")
    service_lines = info.get("serviceLines", [])
    outcome = _outcome_for(control)
    denied = outcome == "denied"
    for transaction in report.get("transactions", []):
        transaction_paid = Decimal("0.00")
        for detail in transaction.get("detailInfo", []):
            for payment in detail.get("paymentInfo", []):
                claim_payment = payment["claimPaymentInfo"]
                posted_lines = (
                    [
                        _paid_line(payment["serviceLines"][0], line, number, outcome)
                        for line, number in zip(service_lines, lines, strict=False)
                    ]
                    if service_lines
                    else []
                )
                if posted_lines:
                    payment["serviceLines"] = posted_lines
                    paid_total = sum(
                        (
                            Decimal(
                                sl["servicePaymentInformation"]["lineItemProviderPaymentAmount"]
                            )
                            for sl in posted_lines
                        ),
                        Decimal("0.00"),
                    )
                    # Every adjustment on the line, whatever group it was
                    # written under. On a `disagreeing` claim the lines wrote
                    # it as CO and the claim still states it here as the
                    # client's — which is the contradiction, stated in one
                    # place and visible as one line of code.
                    patient_total = sum(
                        (
                            Decimal(adjustment["adjustmentAmount1"])
                            for sl in posted_lines
                            for adjustment in sl.get("serviceAdjustments", [])
                        ),
                        Decimal("0.00"),
                    )
                elif charge:
                    paid_total, patient_total = _line_split(Decimal(str(charge)), outcome)
                else:
                    paid_total = patient_total = Decimal("0.00")
                if charge:
                    claim_payment["claimPaymentAmount"] = str(paid_total)
                    claim_payment["totalClaimChargeAmount"] = str(charge)
                claim_payment["patientResponsibilityAmount"] = str(patient_total)
                if denied:
                    claim_payment["claimStatusCode"] = _DENIED_CLAIM_STATUS_CODE
                transaction_paid += paid_total
        transaction["financialInformation"]["totalActualProviderPaymentAmount"] = str(
            transaction_paid
        )
    return report


def _paid_line(
    template: dict[str, Any], line: dict[str, Any], number: str, outcome: Outcome
) -> dict[str, Any]:
    paid = copy.deepcopy(template)
    service = line.get("professionalService", {})
    charge = Decimal(str(service.get("lineItemChargeAmount") or "0"))
    paid_amount, patient_amount = _line_split(charge, outcome)
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
    if service.get("lineItemChargeAmount"):
        payment["lineItemChargeAmount"] = str(charge)
        payment["lineItemProviderPaymentAmount"] = str(paid_amount)
    if service.get("serviceUnitCount"):
        payment["unitsOfServicePaidCount"] = str(service["serviceUnitCount"])
    if patient_amount:
        # THE DELIBERATE DISAGREEMENT, and the only line that makes one.
        #
        # On a `disagreeing` claim the leftover is itemised as a contractual
        # write-off — money nobody owes — while the claim header still states
        # it as the client's share. The line still balances (charge = paid +
        # adjustment) and so does the claim, so the engine's arithmetic
        # checks pass and only the CLP05-vs-itemisation cross-check fires.
        # That is on purpose: one hold, one reason, nothing ambiguous.
        group, reason = (
            (_CONTRACTUAL_GROUP, _CO_REASON_FEE_SCHEDULE)
            if outcome == "disagreeing"
            else (
                _PATIENT_RESPONSIBILITY_GROUP,
                _PR_REASON_NOT_COVERED if outcome == "denied" else _PR_REASON_DEDUCTIBLE,
            )
        )
        paid["serviceAdjustments"] = [
            {
                "claimAdjustmentGroupCode": group,
                "adjustmentReasonCode1": reason,
                "adjustmentAmount1": str(patient_amount),
            }
        ]
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
    # The vendor's webhook event, which is a POINTER and not the thing: an
    # id, a type, and the resource it happened to. The reader fetches the
    # transaction afterwards from the endpoints above. An earlier version of
    # this harness posted an EventBridge-shaped envelope instead —
    # ``source`` / ``detail-type`` / ``detail`` with the whole document
    # inline — which the reader answered 400 for every delivery, and which
    # nothing noticed because no test had ever driven a claim far enough to
    # receive one (PABLO-1qox).
    event = {
        "id": event_id,
        "object": "v1.event",
        "type": "transaction.processed",
        "account": _ACCOUNT_ID,
        "environment": "TEST",
        "created": _now(),
        "resource": {"id": transaction_id, "type": "transaction"},
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
            # The class, not the message, in both places. An httpx error
            # carries the request it failed on, and this request is a signed
            # webhook — so the message can put the signing headers into the
            # log AND into /_fake/received, which hands the whole delivery
            # record back to anyone who asks. A test harness, but the same
            # rule, and the scanner was right to flag it. The class is all a
            # test needs: it asserts that delivery failed, not how.
            delivery["error"] = type(exc).__name__
            # Keyed by event id, not by control number. The event id is what
            # the reader logs on its own side, so the two correlate; the
            # control number carries a patient's claim identity, is already
            # on the delivery record for a test to read, and would be in a
            # log line for no one's benefit.
            logger.warning(
                "webhook delivery failed kind=%s event=%s error=%s",
                kind,
                event_id,
                type(exc).__name__,
            )
    state.webhooks.append(delivery)
    return delivery


# --- transactions (polling + reports) ---------------------------------------


@app.get(f"{CLAIMS}/claims/{{claim_id}}/timeline")
async def claim_timeline(claim_id: str, request: Request) -> Any:
    """One claim's whole life, from the recording of a real one.

    ``claim_timeline_paid_in_full.json`` is a genuine capture — submission,
    the clearinghouse's acknowledgement, and the payer's 835 paying in full —
    so the shapes here are the vendor's own, discriminated union and all. The
    ids and amounts are re-pointed at the claim being asked about; nothing
    about the structure is invented.

    Before this existed the SDK was handed a 404 body and failed to pick a
    variant, which surfaced as a bare ``DiscriminatorError`` every time the
    pipeline ran and told nobody which call had failed (PABLO-ukzm).
    """
    await _record(request)
    control = _control_for_claim_id(claim_id)
    if control is None:
        return _vendor_error(404, "NOT_FOUND", "no claim with that id")
    claim = state.claims.get(control, {})
    lines = _line_control_numbers(claim, control)
    timeline: dict[str, Any] = _deep_replace(
        _load("claim_timeline_paid_in_full.json"),
        {
            _RECORDED_CONTROL_NUMBER: control,
            _RECORDED_LINE_CONTROL_NUMBER: lines[0],
            _RECORDED_CORRELATION_ID: claim_id,
        },
    )
    # Only what has actually happened to this claim. The capture ends at
    # paid; a claim the payer has not answered yet must not read as paid
    # just because the recording did.
    delivered = {entry["kind"] for entry in state.webhooks if entry["control_number"] == control}
    allowed = {"professionalClaimSubmission"}
    if "277" in delivered:
        allowed.add("claimAcknowledgment")
    if "835" in delivered:
        allowed.add("claimPaymentInformation")
    timeline["items"] = [item for item in timeline["items"] if next(iter(item), None) in allowed]
    return timeline


@app.get(f"{CORE}/polling/transactions")
@app.get(f"{CORE}/transactions")
async def list_transactions(request: Request) -> Any:
    """The transaction feed the pipeline's status pass reads.

    Served on BOTH paths on purpose. The adapter polls
    ``/polling/transactions`` (see ``app/claims/stedi.py``), and this harness
    answered only ``/transactions`` — so every pass 404'd and read nothing,
    which is why a claim sat at ``submitted`` with the acknowledgement
    already waiting in the feed (PABLO-ukzm). Keeping the bare path too
    because the recorded fixtures were captured against it.
    """
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


@app.get(f"{HEALTHCARE}/change/medicalnetwork/reports/v2/{{transaction_id}}/{{usage}}")
async def get_report(transaction_id: str, usage: str, request: Request) -> Any:
    """The 277CA or 835 as JSON, on the path the adapter actually asks for.

    The adapter reads reports from the vendor's Change-compatibility report
    endpoint (``.../reports/v2/{id}/277`` and ``.../835``), not from the
    native transaction artifact below. This harness served only the latter,
    so every report fetch 404'd — which the pipeline reported as a claim
    nobody owned rather than as a document it could not read (PABLO-1qox).
    """
    await _record(request)
    entry = state.transactions.get(transaction_id)
    if entry is None:
        return _vendor_error(404, "NOT_FOUND", f"transaction {transaction_id} not found")
    # Answer only for the kind this transaction actually IS. Serving the
    # stored report to whichever usage was asked for would let a caller ask
    # a 277 transaction for its 835 and get one, which is the harness
    # agreeing with a bug instead of catching it.
    kind = entry["document"]["x12"]["metadata"]["transaction"]["transactionSetIdentifier"]
    if usage != kind:
        return _vendor_error(404, "NOT_FOUND", f"transaction {transaction_id} has no {usage}")
    return entry["report"]


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
    """A request the payer is already waiting on the practice over.

    A real one lands ``STEDI_ACTION_REQUIRED`` and only later grows a task,
    on the payer's schedule — which is days, and nothing this harness can
    wait for. So the enrollment starts where the interesting part begins:
    open, with the task attached. Everything after this point is the real
    lifecycle.
    """
    await _record(request)
    return _enrollment_now()


@app.get(f"{ENROLLMENTS}/enrollments")
async def list_enrollments(request: Request) -> Any:
    await _record(request)
    return {"items": [_enrollment_now()], "nextPageToken": None}


# --- enrollment tasks and their documents ----------------------------------
#
# The vendor refuses its enrollment API to test keys outright, so this is the
# only place the task lifecycle can be driven at all. It is built to the
# documented shapes rather than to a recording, and the interesting half is
# the part a recording could not give us anyway: a task that starts open,
# takes a PDF, and closes.
#
# The upload deliberately keeps the vendor's two-step shape. Asking for a
# slot returns a pre-signed URL served by THIS app, and the bytes go there in
# a second request that carries no API key — because the real one goes to the
# vendor's object store, not its API, and code that assumed otherwise would
# work here and fail in production.

#: The one open task the harness offers, in the vendor's mixed shape: a text
#: field and a PDF, which is the case that exercises everything.
_TASK_ID = "task-e2e-0001"

#: The blank form the task links to — on the enrollment before the practice
#: has uploaded anything, which is how the vendor attaches a payer's form.
_TEMPLATE_DOCUMENT_ID = "template-0001"

#: The smallest thing a PDF reader will open, served for any download. The
#: suite asserts that a document comes back at all; what is in it is the
#: payer's business.
_A_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _fake_task() -> dict[str, Any]:
    return {
        "id": _TASK_ID,
        "responsibleParty": "PROVIDER",
        "isComplete": state.task_complete,
        "rank": 0,
        "definition": {
            "manualTask": {
                "instructions": (
                    "Provide your Medicaid Provider Identifier and upload the signed "
                    "provider agreement."
                ),
                "links": [
                    {
                        "label": "Provider Agreement Template",
                        "url": f"{PUBLIC_URL}{ENROLLMENTS}/documents/{_TEMPLATE_DOCUMENT_ID}",
                    }
                ],
                "fields": [
                    {
                        "key": "MEDICAID_ID",
                        "label": "Medicaid Provider Identifier",
                        "fieldType": "TEXT",
                    },
                    {
                        "key": "SIGNED_AGREEMENT",
                        "label": "Signed Provider Agreement",
                        "description": "The agreement, filled in and signed, as a PDF",
                        "fieldType": "DOCUMENT",
                    },
                ],
            }
        },
    }


def _enrollment_now() -> dict[str, Any]:
    """The enrollment as it currently stands, tasks and documents included."""
    record: dict[str, Any] = _load("enrollment_create_enrollment_835.json")
    # Answering the task hands the request to the payer; it does not make
    # it live. That is weeks away and nobody here is waiting for it.
    record["status"] = "PROVISIONING" if state.task_complete else "PROVIDER_ACTION_REQUIRED"
    record["tasks"] = [_fake_task()]
    record["documents"] = list(state.documents.values())
    return record


@app.get(f"{ENROLLMENTS}/enrollments/{{enrollment_id}}")
async def get_enrollment(enrollment_id: str, request: Request) -> Any:
    await _record(request)
    record = _enrollment_now()
    record["id"] = enrollment_id
    return record


@app.post(f"{ENROLLMENTS}/enrollments/{{enrollment_id}}/documents")
async def upload_slot(enrollment_id: str, request: Request) -> Any:
    body = await _record(request)
    name = (body or {}).get("name") or "document.pdf"
    document_id = f"doc-{len(state.documents) + 1:04d}"
    # PENDING until the bytes actually arrive, exactly as the vendor reports
    # it — so a client that completes a task too early fails here too.
    state.documents[document_id] = {
        "id": document_id,
        "name": name,
        "status": "PENDING",
    }
    return {
        "enrollmentId": enrollment_id,
        "uploadUrl": f"{PUBLIC_URL}/_fake/upload/{document_id}",
        "documentId": document_id,
    }


@app.put("/_fake/upload/{document_id}")
async def receive_document(document_id: str, request: Request) -> Any:
    """Stand in for the vendor's object store.

    Under ``/_fake`` on purpose: this is not one of the vendor's API paths,
    and the real upload URL is a signed link somewhere else entirely. No
    ``Authorization`` header is required or expected.
    """
    body = await request.body()
    document = state.documents.get(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="no such document")
    if not body.startswith(b"%PDF"):
        document["status"] = "FAILED"
        raise HTTPException(status_code=400, detail="only PDF documents are supported")
    document["status"] = "UPLOADED"
    document["size"] = len(body)
    # Logged in the same shape as every other request, minus the body: these
    # bytes are a signed practice document, and nothing here needs to hold
    # them once the size and the fact of arrival are recorded.
    state.requests.append(
        {
            "at": _now(),
            "method": "PUT",
            "path": f"/_fake/upload/{document_id}",
            "query": dict(request.query_params),
            "headers": {k.lower(): v for k, v in request.headers.items()},
            "json": None,
            "control_number": None,
            "bytes": len(body),
        }
    )
    return {"ok": True}


@app.get(f"{ENROLLMENTS}/documents/{{document_id}}")
async def document_link(document_id: str, request: Request) -> Any:
    """What a task's own link answers when the key is presented.

    The vendor puts these URLs inside a task, so a client follows one
    verbatim rather than building a path. Answering with a link rather than
    bytes is the vendor's shape, and is why a browser cannot simply be
    pointed at the task link: this hop needs the account key.
    """
    await _record(request)
    if document_id not in state.documents and document_id != _TEMPLATE_DOCUMENT_ID:
        raise HTTPException(status_code=404, detail="no such document")
    return {"downloadUrl": f"{BROWSER_URL}/_fake/download/{document_id}"}


@app.get(f"{ENROLLMENTS}/documents/{{document_id}}/download")
async def download_document(document_id: str, request: Request) -> Any:
    """A link to the bytes, not the bytes.

    The vendor answers with a short-lived URL at its object store; the caller
    follows it without an API key. Same shape here, pointing back at this app
    under ``/_fake`` so nothing can come to depend on the download living on
    a vendor path.
    """
    await _record(request)
    if document_id not in state.documents and document_id != _TEMPLATE_DOCUMENT_ID:
        raise HTTPException(status_code=404, detail="no such document")
    return {"downloadUrl": f"{BROWSER_URL}/_fake/download/{document_id}"}


@app.get("/_fake/download/{document_id}")
async def serve_document(document_id: str) -> Response:
    """Stand in for the vendor's object store on the way back."""
    document = state.documents.get(document_id)
    if document is None and document_id != _TEMPLATE_DOCUMENT_ID:
        raise HTTPException(status_code=404, detail="no such document")
    name = (document or {}).get("name") or "provider-agreement.pdf"
    return Response(
        content=_A_PDF,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@app.post(f"{ENROLLMENTS}/tasks/{{task_id}}")
async def complete_task(task_id: str, request: Request) -> Any:
    body = await _record(request)
    if task_id != _TASK_ID:
        raise HTTPException(status_code=404, detail="no such task")
    values = (((body or {}).get("responseData") or {}).get("manualTask") or {}).get("values") or []
    # Refuse a completion that names a document we never finished taking —
    # the failure this whole ordering exists to prevent.
    for value in values:
        reference = (value.get("value") or {}).get("document") or {}
        document_id = reference.get("documentId")
        if document_id is None:
            continue
        document = state.documents.get(document_id)
        if document is None or document["status"] != "UPLOADED":
            raise HTTPException(status_code=400, detail="document is not uploaded")
    state.task_complete = bool((body or {}).get("completed", True))
    state.task_values = list(values)
    return {"ok": True}


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


@app.post("/_fake/outcome")
async def set_outcome(request: Request) -> Any:
    """Arm what the next 835s will say, before any claim has been filed.

    With ``control_number`` it arms one claim; without, it arms every claim
    that has no prefix of its own. Cleared by ``/_fake/reset``.
    """
    body = await request.json()
    outcome = body.get("outcome")
    if outcome is not None and outcome not in OUTCOMES:
        return JSONResponse({"error": f"outcome must be one of {OUTCOMES}"}, status_code=400)
    control = body.get("control_number")
    if control:
        if outcome is None:
            state.outcomes.pop(str(control), None)
        else:
            state.outcomes[str(control)] = outcome
    else:
        state.default_outcome = outcome
    return {"ok": True, "outcome": outcome, "control_number": control}


@app.post("/_fake/deliver")
async def deliver(request: Request) -> Any:
    """Fire the 277CA or 835 for a control number now instead of on its timer."""
    body = await request.json()
    control = str(body.get("control_number") or "")
    kind = str(body.get("kind") or "")
    outcome = body.get("outcome")
    if not control or kind not in ("277", "835"):
        return JSONResponse(
            {"error": "control_number and kind (277 or 835) are required"}, status_code=400
        )
    if outcome is not None:
        if outcome not in OUTCOMES:
            return JSONResponse({"error": f"outcome must be one of {OUTCOMES}"}, status_code=400)
        # Recorded rather than passed down: a claim's fate is a fact about the
        # claim, and a forced 835 must not say something different from one
        # the timer fires for the same claim a moment later.
        state.outcomes[control] = outcome
    return await _deliver(control, "277" if kind == "277" else "835")
