# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for the two receivers that turn an outside event into money.

``app.routes.payment_webhooks`` is what moves a pending charge to succeeded;
``app.routes.claim_webhooks`` is what moves a claim on an acknowledgement. The
unit suites cover both against a fake transport, which can show what SQL *would*
have been issued but not what the database *did*. The failure modes that matter
here are exactly the ones a fake cannot show:

* a redelivery that answers 200 twice while writing twice — indistinguishable
  from a correct receiver unless you count the rows afterwards;
* a delivery that arrives before the row it names was committed, which to the
  receiver is indistinguishable from a row that will never exist;
* a signature check that passes over a body somebody edited in transit;
* a delivery that reaches into a practice schema it does not belong to.

So every assertion here counts rows, or compares a row against the value it
carried before the delivery. Nothing trusts a status code or a returned
outcome on its own.

Both receivers run for real: their real HMAC verifiers, over a secret this
module generates at import (nothing here is shaped like a credential, and no
vendor's real identifiers appear), against real practice schemas provisioned
from the canonical tenant template, under the suite's NOSUPERUSER NOBYPASSRLS
role. The only fake is the clearinghouse's HTTP side — an outbound call to the
vendor, not part of the delivery path under test.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA_A = f"practice_test_whd_a_{_SUFFIX}"
_SCHEMA_B = f"practice_test_whd_b_{_SUFFIX}"
_PRACTICE_A = f"practice-whd-a-{_SUFFIX}"
_PRACTICE_B = f"practice-whd-b-{_SUFFIX}"
_CLINICIAN_A = str(uuid.uuid4())
_CLINICIAN_B = str(uuid.uuid4())

# Generated per run rather than written down. These are only ever fed to
# hmac.new(), so any bytes do, and a literal shaped like a signing key would be
# indistinguishable from a leaked one to a secret scanner.
_STRIPE_SECRET = secrets.token_hex(24)
_CLEARINGHOUSE_SECRET = secrets.token_hex(24)


# ---------------------------------------------------------------------------
# The two practices
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Practice:
    """One provisioned practice: where its rows live and who may see them."""

    practice_id: str
    schema: str
    clinician: str
    patient_id: str
    coverage_id: str
    payer_id: str


@dataclass(frozen=True)
class Practices:
    a: Practice
    b: Practice


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@contextlib.contextmanager
def _armed(engine: Engine, schema: str, user_id: str) -> Iterator[Connection]:
    """A connection inside the practice schema, armed as one clinician.

    The suite's role is NOBYPASSRLS, so an unarmed connection reads every
    per-client table as empty — an out-of-band row count has to say who it is
    counting as, or it proves nothing.
    """
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": user_id})
        yield conn


def _seed_practice(engine: Engine, practice_id: str, schema: str, clinician: str) -> Practice:
    """Provision the schema, register the practice, and give it one client."""
    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    create_practice_schema(engine, schema)

    email = f"{practice_id}@example.test"
    now = datetime.now(UTC)
    with OrmSession(bind=engine) as platform_session:
        platform_session.add(
            PlatformUserRow(id=clinician, email=email, name="Practice Owner", created_at=now)
        )
        platform_session.add(
            PracticeRow(
                id=practice_id,
                name=f"Practice {practice_id}",
                schema_name=schema,
                owner_email=email,
                owner_user_id=clinician,
                created_at=now,
            )
        )
        # The claims fan-out asks the platform who a practice's clinicians are,
        # and falls back to EVERY platform user when a practice has no mapping
        # rows. Other modules in this suite leave users behind, so map ours
        # explicitly rather than letting the fan-out guess.
        platform_session.add(
            EmailTenantMappingRow(
                email=email, tenant_id=practice_id, practice_id=practice_id, created_at=now
            )
        )
        platform_session.commit()

    patient_id = str(uuid.uuid4())
    with _armed(engine, schema, clinician) as conn:
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', 'test', 'patient', "
                "'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": clinician},
        )

    coverage_id, payer_id = _seed_coverage(engine, schema, clinician, patient_id)
    return Practice(
        practice_id=practice_id,
        schema=schema,
        clinician=clinician,
        patient_id=patient_id,
        coverage_id=coverage_id,
        payer_id=payer_id,
    )


def _seed_coverage(engine: Engine, schema: str, clinician: str, patient_id: str) -> tuple[str, str]:
    """A payer and an active coverage, the two rows a claim must point at."""
    from app.models.coverage import PatientCoverage  # noqa: PLC0415
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from app.services.coverage_intake import new_payer  # noqa: PLC0415

    with _tenant_session(engine, schema, clinician) as session:
        payer = PostgresPayerRepository(session).create(
            new_payer(name="Test Payer", payer_id="TESTPAYER")
        )
        now = datetime.now(UTC)
        coverage = PostgresPatientCoverageRepository(session).create(
            PatientCoverage(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                payer_id=payer.id,
                member_id="123456789",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        return coverage.id, payer.id


@contextlib.contextmanager
def _tenant_session(engine: Engine, schema: str, user_id: str) -> Iterator[Any]:
    """An ORM session armed the way an off-request worker opens one."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    try:
        session.execute(text(f"SET search_path = {schema}, platform, public"))
        arm_current_user_id(session, user_id)
        yield session
    finally:
        session.close()
        _current_user_id.reset(uid_token)
        _current_tenant_schema.reset(schema_token)


@pytest.fixture(scope="module")
def practices(engine: Engine) -> Iterator[Practices]:
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    both = Practices(
        a=_seed_practice(engine, _PRACTICE_A, _SCHEMA_A, _CLINICIAN_A),
        b=_seed_practice(engine, _PRACTICE_B, _SCHEMA_B, _CLINICIAN_B),
    )
    yield both

    from app.db.platform_models import (  # noqa: PLC0415
        EmailTenantMappingRow,
        PlatformUserRow,
        PracticeRow,
    )
    from sqlalchemy import delete  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(bind=engine) as session:
        for practice in (both.a, both.b):
            session.execute(delete(PracticeRow).where(PracticeRow.id == practice.practice_id))
            session.execute(
                delete(EmailTenantMappingRow).where(
                    EmailTenantMappingRow.practice_id == practice.practice_id
                )
            )
            session.execute(delete(PlatformUserRow).where(PlatformUserRow.id == practice.clinician))
        session.commit()
    with engine.connect() as conn:
        for schema in (_SCHEMA_A, _SCHEMA_B):
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


# ---------------------------------------------------------------------------
# The card processor's side
# ---------------------------------------------------------------------------


class _StripeSettings:
    """Only the fields the receiver reads."""

    def __init__(self) -> None:
        self.stripe_patient_billing_webhook_secret = SecretStr(_STRIPE_SECRET)
        self.stripe_patient_billing_webhook_secret_previous = SecretStr("")


@pytest.fixture(scope="module")
def payment_app(practices: Practices) -> Iterator[FastAPI]:
    """The receiver mounted on the real database, with a locally generated secret.

    Only the settings lookup is redirected: the signature verifier, the
    practice lookup, the ledger update and the dedupe ledger are the shipped
    ones, running against the container's Postgres.
    """
    from app.routes import payment_webhooks  # noqa: PLC0415

    del practices
    with pytest.MonkeyPatch.context() as mp:
        settings = _StripeSettings()
        mp.setattr(payment_webhooks, "get_settings", lambda: settings)
        # ``reconcile`` takes settings as a parameter from the route rather than
        # importing ``get_settings``, so there is no name here to patch.
        app = FastAPI()
        app.include_router(payment_webhooks.router)
        yield app


@pytest.fixture(scope="module")
def payments(payment_app: FastAPI) -> TestClient:
    return TestClient(payment_app, raise_server_exceptions=False)


def _stripe_signature(body: bytes, *, timestamp: int | None = None) -> str:
    stamp = int(time.time()) if timestamp is None else timestamp
    signed = f"{stamp}.".encode() + body
    digest = hmac.new(_STRIPE_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _payment_body(event_id: str, event_type: str, obj: dict[str, Any]) -> bytes:
    event = {
        "id": event_id,
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": obj},
    }
    return json.dumps(event).encode()


def _ours(payment_intent_id: str, practice: Practice, charge_id: str) -> dict[str, Any]:
    """A PaymentIntent carrying the metadata the charge route stamps on one."""
    from app.payments.reconcile import (  # noqa: PLC0415
        METADATA_CHARGE_ID,
        METADATA_PRACTICE_ID,
        METADATA_USER_ID,
    )

    return {
        "id": payment_intent_id,
        "metadata": {
            METADATA_CHARGE_ID: charge_id,
            METADATA_USER_ID: practice.clinician,
            METADATA_PRACTICE_ID: practice.practice_id,
        },
    }


def _deliver_payment(client: TestClient, body: bytes, *, signature: str | None = None) -> Any:
    from app.routes.payment_webhooks import PAYMENT_WEBHOOK_PATH  # noqa: PLC0415

    headers = {"content-type": "application/json"}
    if signature is None:
        signature = _stripe_signature(body)
    if signature:
        headers["Stripe-Signature"] = signature
    return client.post(PAYMENT_WEBHOOK_PATH, content=body, headers=headers)


def _seed_charge(engine: Engine, practice: Practice, payment_intent_id: str) -> str:
    """A pending charge row, the state the synchronous call leaves behind."""
    charge_id = str(uuid.uuid4())
    with _armed(engine, practice.schema, practice.clinician) as conn:
        conn.execute(
            text(
                "INSERT INTO patient_charges (id, patient_id, kind, amount_cents, currency, "
                "status, stripe_payment_intent_id, created_by_user_id, created_at) "
                "VALUES (:id, CAST(:pid AS uuid), 'session', 12500, 'usd', 'pending', "
                ":pi, :uid, now())"
            ),
            {
                "id": charge_id,
                "pid": practice.patient_id,
                "pi": payment_intent_id,
                "uid": practice.clinician,
            },
        )
    return charge_id


def _charges(engine: Engine, practice: Practice, payment_intent_id: str) -> list[Any]:
    """Every ledger row in this practice for that PaymentIntent, as its owner sees them."""
    with _armed(engine, practice.schema, practice.clinician) as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id, status, status_detail, updated_at, fee_cents, net_cents "
                    "FROM patient_charges WHERE stripe_payment_intent_id = :pi"
                ),
                {"pi": payment_intent_id},
            ).all()
        )


def _processed_events(engine: Engine, event_id: str) -> list[Any]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT event_type, practice_id FROM platform.processed_payment_events "
                    "WHERE event_id = :e"
                ),
                {"e": event_id},
            ).all()
        )


# ---------------------------------------------------------------------------
# The clearinghouse's side
# ---------------------------------------------------------------------------


class _ClearinghouseSettings:
    def __init__(self) -> None:
        self.clearinghouse_webhook_secret = SecretStr(_CLEARINGHOUSE_SECRET)
        self.clearinghouse_webhook_secret_previous = SecretStr("")


@dataclass
class ClaimRig:
    """The mounted receiver plus each practice's vendor account."""

    client: TestClient
    vendors: dict[str, Any]


@pytest.fixture(scope="module")
def claims(practices: Practices) -> Iterator[ClaimRig]:
    """The receiver mounted on the real database and the real fan-out.

    Two things are redirected and nothing else: where the destination secret
    comes from, and which HTTP client a practice's clearinghouse account
    answers on. The practice registry, the tenant sessions, the claim
    repositories and the receipt ledger are the shipped ones on real Postgres.
    """
    from app.claims import fanout  # noqa: PLC0415
    from app.routes import claim_webhooks  # noqa: PLC0415
    from tests.claims_pipeline_fakes import FakeClearinghouse  # noqa: PLC0415

    # ONE account, as the deployment has: the credential provider resolves the
    # deployment's own key without regard to which practice is asking, and the
    # receiver reads through it with no practice in hand at all (``None``).
    # Giving each practice its own fake would model a topology that does not
    # exist and would quietly make the account, rather than the routing index,
    # look like what separates two practices' claims.
    account = FakeClearinghouse()
    vendors = {
        None: account,
        practices.a.practice_id: account,
        practices.b.practice_id: account,
    }
    with pytest.MonkeyPatch.context() as mp:
        settings = _ClearinghouseSettings()
        mp.setattr(claim_webhooks, "get_settings", lambda: settings)
        mp.setattr(fanout, "clearinghouse_client_for_practice", vendors.get)
        app = FastAPI()
        app.include_router(claim_webhooks.router)
        yield ClaimRig(client=TestClient(app, raise_server_exceptions=False), vendors=vendors)


def _clearinghouse_headers(body: bytes, *, timestamp: int | None = None) -> dict[str, str]:
    """The vendor's documented signature over ``"<timestamp>.<body>"``."""
    stamp = int(time.time()) if timestamp is None else timestamp
    signed = f"{stamp}.".encode() + body
    mac = hmac.new(_CLEARINGHOUSE_SECRET.encode(), signed, hashlib.sha256)
    digest = base64.b64encode(mac.digest())
    return {
        "content-type": "application/json",
        "webhook-signature": f"v1,{digest.decode()}",
        "webhook-timestamp": str(stamp),
        "webhook-id": f"msg_{uuid.uuid4().hex}",
    }


def _claim_body(event_id: str, transaction_id: str) -> bytes:
    event = {
        "id": event_id,
        "type": "transaction.processed",
        "resource": {"type": "transaction", "id": transaction_id},
    }
    return json.dumps(event).encode()


def _deliver_claim(rig: ClaimRig, body: bytes, *, headers: dict[str, str] | None = None) -> Any:
    from app.routes.claim_webhooks import CLEARINGHOUSE_WEBHOOK_PATH  # noqa: PLC0415

    return rig.client.post(
        CLEARINGHOUSE_WEBHOOK_PATH,
        content=body,
        headers=_clearinghouse_headers(body) if headers is None else headers,
    )


def _seed_submitted_claim(engine: Engine, practice: Practice, control_number: str) -> str:
    """A submitted claim owned by the practice's clinician, committed."""
    with _tenant_session(engine, practice.schema, practice.clinician) as session:
        claim_id = _add_claim(session, practice, control_number)
        session.commit()
    return claim_id


def _add_claim(session: Any, practice: Practice, control_number: str) -> str:
    """Insert a submitted claim into the open session; the caller commits.

    Also records the routing index row, because that is what filing does: the
    receiver finds a claim's practice by looking the control number up in
    ``platform.claim_routes``, and a claim with no row there is one the
    receiver has no way to place. Written on its own connection and committed
    immediately — deliberately, and faithfully, since production writes it
    alongside the outbox's pending marker, BEFORE the claim's state moves.
    """
    from app.claims.routing import record_claim_route  # noqa: PLC0415
    from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415
    from tests.claims_fixtures import billing_snapshot, claim, line  # noqa: PLC0415

    record_claim_route(control_number, practice.practice_id, practice.clinician)

    claim_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    created = PostgresClaimRepository(session).create(
        claim(
            id=claim_id,
            control_number=control_number,
            patient_id=practice.patient_id,
            coverage_id=practice.coverage_id,
            payer_id=practice.payer_id,
            state="submitted",
            submitted_at=now,
            billing_snapshot=billing_snapshot(user_id=practice.clinician),
            created_at=now,
            updated_at=now,
            lines=[
                line(
                    id=str(uuid.uuid4()),
                    claim_id=claim_id,
                    patient_id=practice.patient_id,
                    line_control_number=f"{control_number}L1",
                    created_at=now,
                )
            ],
        )
    )
    return created.id


def _claim_state(engine: Engine, practice: Practice, claim_id: str) -> str | None:
    with _armed(engine, practice.schema, practice.clinician) as conn:
        row = conn.execute(
            text("SELECT state FROM claims WHERE id = CAST(:c AS uuid)"), {"c": claim_id}
        ).first()
        return None if row is None else str(row[0])


def _claim_event_count(engine: Engine, practice: Practice, claim_id: str) -> int:
    with _armed(engine, practice.schema, practice.clinician) as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM claim_events WHERE claim_id = CAST(:c AS uuid)"),
                {"c": claim_id},
            ).scalar_one()
        )


def _control_number() -> str:
    from tests.claims_pipeline_fakes import fresh_control_number  # noqa: PLC0415

    return str(fresh_control_number())


# ---------------------------------------------------------------------------
# The card processor's deliveries
# ---------------------------------------------------------------------------


class TestPaymentIdempotence:
    def test_a_redelivered_event_moves_the_row_once_and_records_one_ledger_entry(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """The second delivery must write nothing — proved by row counts, not by
        the ``deduped`` flag the endpoint answers with."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_id = _seed_charge(engine, practices.a, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"
        body = _payment_body(
            event_id, "payment_intent.succeeded", _ours(payment_intent, practices.a, charge_id)
        )

        first = _deliver_payment(payments, body)
        after_first = _charges(engine, practices.a, payment_intent)
        second = _deliver_payment(payments, body)
        after_second = _charges(engine, practices.a, payment_intent)

        assert (first.status_code, second.status_code) == (200, 200)
        assert second.json().get("deduped") == "true"
        # One row, one state change: the second delivery did not touch
        # updated_at, which the UPDATE would have moved had it run.
        assert len(after_first) == 1
        assert after_first[0].status == "succeeded"
        assert after_second == after_first
        assert len(_processed_events(engine, event_id)) == 1

    def test_a_resend_under_a_fresh_event_id_is_refused_by_the_status_guard(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """Dedupe by event id is only the first line: a processor that resends the
        same outcome under a NEW id gets past it, and the ledger's own status
        guard is what keeps the row at one state change."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_id = _seed_charge(engine, practices.a, payment_intent)
        obj = _ours(payment_intent, practices.a, charge_id)

        first_id, second_id = f"evt_{uuid.uuid4().hex}", f"evt_{uuid.uuid4().hex}"
        _deliver_payment(payments, _payment_body(first_id, "payment_intent.succeeded", obj))
        after_first = _charges(engine, practices.a, payment_intent)
        resend = _deliver_payment(
            payments, _payment_body(second_id, "payment_intent.succeeded", obj)
        )
        after_resend = _charges(engine, practices.a, payment_intent)

        assert resend.status_code == 200
        assert after_resend == after_first, "the guard refused the transition, so nothing moved"
        # Both events are recorded — a refused transition is handled, not failed,
        # and leaving it unrecorded would buy a redelivery that can only be
        # refused again.
        assert len(_processed_events(engine, first_id)) == 1
        assert len(_processed_events(engine, second_id)) == 1

    def test_the_database_refuses_a_second_row_for_one_event_id(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """The dedupe key is enforced by the database, not only by the read that
        precedes the write."""
        from app.payments.reconcile import record_processed_event  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_id = _seed_charge(engine, practices.a, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"
        _deliver_payment(
            payments,
            _payment_body(
                event_id, "payment_intent.succeeded", _ours(payment_intent, practices.a, charge_id)
            ),
        )

        with pytest.raises(IntegrityError):
            record_processed_event(
                event_id=event_id,
                event_type="payment_intent.succeeded",
                practice_id=practices.a.practice_id,
                created=None,
            )
        assert len(_processed_events(engine, event_id)) == 1

    def test_two_simultaneous_deliveries_of_one_event_move_the_row_once(
        self, engine: Engine, practices: Practices, payment_app: FastAPI
    ) -> None:
        """The dedupe read and the dedupe write are two statements in two
        transactions, so a processor delivering the same event twice at once can
        get both past the read. What must still hold — and what only a real
        database can show — is that ``SELECT … FOR UPDATE`` lets exactly one of
        them move the row, and the primary key lets exactly one record it.

        Observed on Postgres: the ledger comes out right, and the delivery that
        loses the race answers **500**, because ``record_processed_event`` inserts
        the dedupe row unconditionally and the primary key raises
        ``UniqueViolation`` on the second insert. That is a self-healing 5xx —
        the processor redelivers, ``event_already_processed`` now sees the row,
        and the retry is a clean 200 — so it costs an unhandled-exception log and
        a retry rather than a wrong ledger. The assertions below are on the
        invariants, deliberately not on that status, so they neither bless the
        500 nor break when the insert learns to tolerate the conflict."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_id = _seed_charge(engine, practices.a, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"
        body = _payment_body(
            event_id, "payment_intent.succeeded", _ours(payment_intent, practices.a, charge_id)
        )
        signature = _stripe_signature(body)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [
                future.result()
                for future in [
                    pool.submit(
                        _deliver_payment,
                        TestClient(payment_app, raise_server_exceptions=False),
                        body,
                        signature=signature,
                    )
                    for _ in range(2)
                ]
            ]

        statuses = sorted(response.status_code for response in responses)
        rows = _charges(engine, practices.a, payment_intent)
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert len(_processed_events(engine, event_id)) == 1
        assert statuses.count(200) >= 1, f"neither delivery was acknowledged: {statuses}"


class TestPaymentOrdering:
    def test_a_delivery_that_beats_its_charge_row_creates_nothing_and_asks_to_be_resent(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """The charge route's INSERT and the processor's callback race. A delivery
        that arrives while the row is still uncommitted must park — 503 so the
        processor redelivers — and must leave nothing behind that a later
        redelivery would trip over."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        event_id = f"evt_{uuid.uuid4().hex}"
        charge_id = str(uuid.uuid4())

        connection = engine.connect()
        transaction = connection.begin()
        try:
            connection.execute(text(f"SET search_path = {practices.a.schema}, platform, public"))
            connection.execute(
                text("SELECT set_config('app.current_user_id', :u, true)"),
                {"u": practices.a.clinician},
            )
            connection.execute(
                text(
                    "INSERT INTO patient_charges (id, patient_id, kind, amount_cents, "
                    "currency, status, stripe_payment_intent_id, created_by_user_id, "
                    "created_at) VALUES (:id, CAST(:pid AS uuid), 'session', 12500, 'usd', "
                    "'pending', :pi, :uid, now())"
                ),
                {
                    "id": charge_id,
                    "pid": practices.a.patient_id,
                    "pi": payment_intent,
                    "uid": practices.a.clinician,
                },
            )

            body = _payment_body(
                event_id,
                "payment_intent.succeeded",
                _ours(payment_intent, practices.a, charge_id),
            )
            early = _deliver_payment(payments, body)

            assert early.status_code == 503
            assert _charges(engine, practices.a, payment_intent) == [], "no phantom row"
            assert _processed_events(engine, event_id) == [], (
                "an unreconciled event must stay unrecorded, or the redelivery "
                "that would fix it never comes"
            )
            transaction.commit()
        finally:
            connection.close()

        redelivered = _deliver_payment(payments, body)

        assert redelivered.status_code == 200
        rows = _charges(engine, practices.a, payment_intent)
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert len(_processed_events(engine, event_id)) == 1

    def test_a_delivery_for_a_charge_that_never_existed_creates_no_row(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        payment_intent = f"pi_{uuid.uuid4().hex}"
        event_id = f"evt_{uuid.uuid4().hex}"
        body = _payment_body(
            event_id,
            "payment_intent.succeeded",
            _ours(payment_intent, practices.a, str(uuid.uuid4())),
        )

        response = _deliver_payment(payments, body)

        assert response.status_code == 503
        assert _charges(engine, practices.a, payment_intent) == []
        assert _processed_events(engine, event_id) == []


class TestPaymentSignature:
    def test_a_body_edited_after_signing_never_reaches_the_ledger(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """The signature is over the raw bytes, so re-pointing the event at a
        different PaymentIntent invalidates it."""
        signed_intent = f"pi_{uuid.uuid4().hex}"
        target_intent = f"pi_{uuid.uuid4().hex}"
        _seed_charge(engine, practices.a, signed_intent)
        target_charge = _seed_charge(engine, practices.a, target_intent)
        event_id = f"evt_{uuid.uuid4().hex}"

        signed_body = _payment_body(
            event_id,
            "payment_intent.succeeded",
            _ours(signed_intent, practices.a, str(uuid.uuid4())),
        )
        tampered_body = _payment_body(
            event_id,
            "payment_intent.succeeded",
            _ours(target_intent, practices.a, target_charge),
        )
        before = _charges(engine, practices.a, target_intent)

        response = _deliver_payment(
            payments, tampered_body, signature=_stripe_signature(signed_body)
        )

        assert response.status_code == 401
        assert _charges(engine, practices.a, target_intent) == before
        assert _charges(engine, practices.a, signed_intent)[0].status == "pending"
        assert _processed_events(engine, event_id) == []

    def test_the_signature_is_checked_before_the_body_is_parsed(self, payments: TestClient) -> None:
        """A body that is not JSON at all answers 401 without a signature and 400
        with one — so the parse cannot be what rejected the unsigned request."""
        garbage = b"\x00 not json {"

        unsigned = _deliver_payment(payments, garbage, signature="")
        signed = _deliver_payment(payments, garbage)

        assert unsigned.status_code == 401
        assert signed.status_code == 400

    def test_a_stale_but_correctly_signed_delivery_never_reaches_the_ledger(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """A replay captured off the wire carries a real signature; the timestamp
        in it is what makes it refusable."""
        from app.payments.reconcile import SIGNATURE_TOLERANCE_SECONDS  # noqa: PLC0415

        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_id = _seed_charge(engine, practices.a, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"
        body = _payment_body(
            event_id, "payment_intent.succeeded", _ours(payment_intent, practices.a, charge_id)
        )
        stale = int(time.time()) - (SIGNATURE_TOLERANCE_SECONDS + 60)

        response = _deliver_payment(
            payments, body, signature=_stripe_signature(body, timestamp=stale)
        )

        assert response.status_code == 401
        assert _charges(engine, practices.a, payment_intent)[0].status == "pending"
        assert _processed_events(engine, event_id) == []


class TestPaymentTenancy:
    def test_a_delivery_moves_only_the_practice_it_names(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """The PaymentIntent id is unique per schema, not per deployment. Two
        practices holding the same id is the shape that would let a delivery walk
        into the wrong ledger, so the practice the processor signed is what has
        to pin the schema."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_a = _seed_charge(engine, practices.a, payment_intent)
        _seed_charge(engine, practices.b, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"
        before_b = _charges(engine, practices.b, payment_intent)

        response = _deliver_payment(
            payments,
            _payment_body(
                event_id, "payment_intent.succeeded", _ours(payment_intent, practices.a, charge_a)
            ),
        )

        assert response.status_code == 200
        assert _charges(engine, practices.a, payment_intent)[0].status == "succeeded"
        assert _charges(engine, practices.b, payment_intent) == before_b
        assert _processed_events(engine, event_id) == [
            ("payment_intent.succeeded", practices.a.practice_id)
        ]

    def test_a_delivery_naming_the_wrong_practice_reaches_no_ledger_at_all(
        self, engine: Engine, practices: Practices, payments: TestClient
    ) -> None:
        """A charge that exists only in practice A, delivered under practice B's
        id, must not fall back to searching every schema."""
        payment_intent = f"pi_{uuid.uuid4().hex}"
        charge_a = _seed_charge(engine, practices.a, payment_intent)
        event_id = f"evt_{uuid.uuid4().hex}"

        response = _deliver_payment(
            payments,
            _payment_body(
                event_id, "payment_intent.succeeded", _ours(payment_intent, practices.b, charge_a)
            ),
        )

        assert response.status_code == 503
        assert _charges(engine, practices.a, payment_intent)[0].status == "pending"
        assert _charges(engine, practices.b, payment_intent) == []
        assert _processed_events(engine, event_id) == []


# ---------------------------------------------------------------------------
# The clearinghouse's deliveries
# ---------------------------------------------------------------------------


class TestClaimIdempotence:
    def test_a_redelivered_event_moves_the_claim_once_and_writes_one_receipt(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        control = _control_number()
        claim_id = _seed_submitted_claim(engine, practices.a, control)
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)
        body = _claim_body(f"evt_{uuid.uuid4().hex}", transaction)

        first = _deliver_claim(claims, body)
        second = _deliver_claim(claims, body)

        assert (first.status_code, second.status_code) == (200, 200)
        assert (first.json()["outcome"], second.json()["outcome"]) == ("moved", "duplicate")
        assert _claim_state(engine, practices.a, claim_id) == "payer_accepted"
        assert _claim_event_count(engine, practices.a, claim_id) == 1

    def test_the_same_acknowledgement_under_a_fresh_event_id_writes_no_second_receipt(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        """The vendor's event id is not the only replay key — the poll and the
        webhook can both bring the same 277CA, under different event ids or none
        at all, so the transaction id has to dedupe too."""
        control = _control_number()
        claim_id = _seed_submitted_claim(engine, practices.a, control)
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)

        first = _deliver_claim(claims, _claim_body(f"evt_{uuid.uuid4().hex}", transaction))
        second = _deliver_claim(claims, _claim_body(f"evt_{uuid.uuid4().hex}", transaction))

        assert (first.json()["outcome"], second.json()["outcome"]) == ("moved", "duplicate")
        assert _claim_state(engine, practices.a, claim_id) == "payer_accepted"
        assert _claim_event_count(engine, practices.a, claim_id) == 1

    def test_the_database_refuses_a_second_receipt_for_one_event_id(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        from app.models.claims import ClaimReceipt  # noqa: PLC0415
        from app.repositories.postgres.claim_receipts import (  # noqa: PLC0415
            PostgresClaimReceiptRepository,
        )
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        control = _control_number()
        claim_id = _seed_submitted_claim(engine, practices.a, control)
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)
        event_id = f"evt_{uuid.uuid4().hex}"
        _deliver_claim(claims, _claim_body(event_id, transaction))

        with _tenant_session(engine, practices.a.schema, practices.a.clinician) as session:
            with pytest.raises(IntegrityError):
                PostgresClaimReceiptRepository(session).add(
                    ClaimReceipt(
                        id=str(uuid.uuid4()),
                        claim_id=claim_id,
                        kind="acknowledged",
                        vendor_event_id=event_id,
                        occurred_at=datetime.now(UTC),
                    )
                )
            session.rollback()
        assert _claim_event_count(engine, practices.a, claim_id) == 1


class TestClaimOrdering:
    def test_a_delivery_that_beats_its_claim_row_writes_nothing(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        """The clearinghouse can answer before the submitting transaction has
        committed. The receiver must treat that as "no claim of ours" and leave
        nothing behind — a later redelivery still has to work."""
        control = _control_number()
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)
        body = _claim_body(f"evt_{uuid.uuid4().hex}", transaction)

        with _tenant_session(engine, practices.a.schema, practices.a.clinician) as session:
            claim_id = _add_claim(session, practices.a, control)
            session.flush()
            early = _deliver_claim(claims, body)
            assert early.status_code == 200
            assert early.json()["outcome"] == "unmatched"
            assert _claim_state(engine, practices.a, claim_id) is None, "no phantom claim"
            session.commit()

        assert _claim_state(engine, practices.a, claim_id) == "submitted"
        assert _claim_event_count(engine, practices.a, claim_id) == 0

        redelivered = _deliver_claim(claims, _claim_body(f"evt_{uuid.uuid4().hex}", transaction))

        assert redelivered.json()["outcome"] == "moved"
        assert _claim_state(engine, practices.a, claim_id) == "payer_accepted"
        assert _claim_event_count(engine, practices.a, claim_id) == 1

    def test_a_delivery_naming_no_claim_of_ours_writes_nothing(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        transaction = claims.vendors[practices.a.practice_id].acknowledge(
            "payer_accepted", _control_number()
        )
        before = _claim_events_in(engine, practices.a)

        response = _deliver_claim(claims, _claim_body(f"evt_{uuid.uuid4().hex}", transaction))

        assert response.status_code == 200
        assert response.json()["outcome"] == "unmatched"
        assert _claim_events_in(engine, practices.a) == before


class TestClaimSignature:
    def test_a_body_edited_after_signing_never_reaches_the_claim(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        control = _control_number()
        claim_id = _seed_submitted_claim(engine, practices.a, control)
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)

        decoy = _claim_body(f"evt_{uuid.uuid4().hex}", f"txn_{uuid.uuid4().hex}")
        tampered = _claim_body(f"evt_{uuid.uuid4().hex}", transaction)
        response = _deliver_claim(claims, tampered, headers=_clearinghouse_headers(decoy))

        assert response.status_code == 401
        assert _claim_state(engine, practices.a, claim_id) == "submitted"
        assert _claim_event_count(engine, practices.a, claim_id) == 0

    def test_the_signature_is_checked_before_the_body_is_parsed(self, claims: ClaimRig) -> None:
        garbage = b"\x00 not json {"

        unsigned = _deliver_claim(claims, garbage, headers={"content-type": "application/json"})
        signed = _deliver_claim(claims, garbage)

        assert unsigned.status_code == 401
        assert signed.status_code == 400

    def test_a_stale_but_correctly_signed_delivery_never_reaches_the_claim(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        from app.claims.webhooks import SIGNATURE_TOLERANCE_SECONDS  # noqa: PLC0415

        control = _control_number()
        claim_id = _seed_submitted_claim(engine, practices.a, control)
        transaction = claims.vendors[practices.a.practice_id].acknowledge("payer_accepted", control)
        body = _claim_body(f"evt_{uuid.uuid4().hex}", transaction)
        stale = int(time.time()) - (SIGNATURE_TOLERANCE_SECONDS + 60)

        response = _deliver_claim(
            claims, body, headers=_clearinghouse_headers(body, timestamp=stale)
        )

        assert response.status_code == 401
        assert _claim_state(engine, practices.a, claim_id) == "submitted"
        assert _claim_event_count(engine, practices.a, claim_id) == 0


class TestClaimTenancy:
    def test_a_delivery_moves_only_the_practice_that_filed_the_claim(
        self, engine: Engine, practices: Practices, claims: ClaimRig
    ) -> None:
        """An acknowledgement must never move another practice's claim.

        Two practices, two live claims, one delivery. What decides is the
        routing index: the practice that filed this control number is the only
        one opened, so the other practice's claim is not merely left alone, it
        is never looked at.
        """
        control_a, control_b = _control_number(), _control_number()
        claim_a = _seed_submitted_claim(engine, practices.a, control_a)
        claim_b = _seed_submitted_claim(engine, practices.b, control_b)
        transaction = claims.vendors[practices.b.practice_id].acknowledge(
            "payer_accepted", control_b
        )

        response = _deliver_claim(claims, _claim_body(f"evt_{uuid.uuid4().hex}", transaction))

        assert response.json()["outcome"] == "moved"
        assert _claim_state(engine, practices.b, claim_b) == "payer_accepted"
        assert _claim_event_count(engine, practices.b, claim_b) == 1
        assert _claim_state(engine, practices.a, claim_a) == "submitted"
        assert _claim_event_count(engine, practices.a, claim_a) == 0

    def test_one_control_number_cannot_be_owned_by_two_practices(
        self, practices: Practices
    ) -> None:
        """The index is keyed globally, and the first filer keeps the number.

        Routing by control number needs it to identify a claim across the whole
        deployment, not just inside a practice. Production earns that: a control
        number is the build instant in base32 followed by six random Crockford
        characters, so two practices collide only by drawing the same six inside
        the same second (see ``app.claims.assembly.new_control_number``).

        Should it ever happen anyway, the primary key decides it once, at write
        time, rather than leaving it to whichever practice a search reached
        first. The loser's row is refused and logged, not silently overwritten —
        an overwrite would send the FIRST practice's remittance to the second
        practice's ledger.
        """
        from app.claims.routing import (  # noqa: PLC0415
            record_claim_route,
            route_for_control_numbers,
        )

        control = _control_number()
        record_claim_route(control, practices.a.practice_id, practices.a.clinician)
        record_claim_route(control, practices.b.practice_id, practices.b.clinician)

        route = route_for_control_numbers([control])
        assert route is not None
        assert route.practice_id == practices.a.practice_id
        assert route.user_id == practices.a.clinician


def _claim_events_in(engine: Engine, practice: Practice) -> int:
    with _armed(engine, practice.schema, practice.clinician) as conn:
        return int(conn.execute(text("SELECT count(*) FROM claim_events")).scalar_one())
