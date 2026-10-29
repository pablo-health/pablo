# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two write-offs racing for one balance cannot together exceed it.

``PostgresPatientPaymentRepository.add_write_off`` reads the client's
balance — arithmetic over the ledger, stored nowhere — and then inserts the
row. Before it took a lock, two requests could both read the full balance,
both pass the check, and leave the client with more written off than was
ever owed. The per-client ``pg_advisory_xact_lock`` makes the second read
wait for the first commit, and this module asks a real engine to prove it:
two threads, two sessions, two 700c write-offs against a 1000c balance.
Exactly one lands, the other is refused as exceeding the balance, and the
ledger shows 700 written off and 300 still owed.

Threads use their own connections because the race is between transactions;
a shared session could not reproduce it.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

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

_CLINICIAN = "5e7b4e2c-0a4f-5cbd-8f0a-0a6b9e5f5e05"
_TENANT_EMAIL = "write-off-race@example.com"
_BALANCE_CENTS = 1000
_WRITE_OFF_CENTS = 700


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """A schema built by the real provisioning path, so the lock runs on shipped DDL."""
    from tests_integration.database.test_tenant_resolution_db import (  # noqa: PLC0415
        _seeded_practice,
    )

    with _seeded_practice(engine, _TENANT_EMAIL) as schema:
        yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _armed(engine: Engine, schema: str) -> Connection:
    """A connection in ``schema`` with the RLS principal armed. Caller closes it.

    Committed before it is handed back: a ``Session`` bound to a connection
    that is already mid-transaction will not commit that transaction, and a
    write-off whose commit never reaches the server holds its lock until the
    connection closes — which would make the second thread wait, see a rolled
    back ledger, and write a second row the test should never see.
    """
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
    conn.commit()
    return conn


@pytest.fixture
def patient_owing_1000(engine: Engine, tenant_schema: str) -> str:
    """One client, granted to the clinician, owing exactly ``_BALANCE_CENTS``."""
    from tests_integration.database.test_patient_charges_db import _seed_patient  # noqa: PLC0415

    pid = _seed_patient(engine, tenant_schema, _CLINICIAN, "Raceworth")
    conn = _armed(engine, tenant_schema)
    try:
        conn.execute(
            text(
                "INSERT INTO patient_charges "
                "(id, patient_id, kind, amount_cents, currency, status, "
                " created_by_user_id, created_at) "
                "VALUES (:id, CAST(:pid AS uuid), 'patient_resp', :amount, 'usd', "
                " 'succeeded', :user, now())"
            ),
            {"id": uuid.uuid4().hex, "pid": pid, "amount": _BALANCE_CENTS, "user": _CLINICIAN},
        )
        conn.commit()
    finally:
        conn.close()
    return pid


def test_two_concurrent_write_offs_cannot_together_exceed_the_balance(
    engine: Engine, tenant_schema: str, patient_owing_1000: str
) -> None:
    from app.payments.balance import patient_balance  # noqa: PLC0415
    from app.repositories.patient_payment import WriteOffExceedsBalanceError  # noqa: PLC0415
    from app.repositories.postgres.patient_payment import (  # noqa: PLC0415
        PostgresPatientPaymentRepository,
    )

    n_threads = 2
    barrier = threading.Barrier(n_threads)
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def write_off() -> None:
        # Bound to one connection so ``search_path`` and the RLS principal
        # survive the commit inside ``add_ledger_row``; a pooled session
        # could come back on a different, unarmed connection.
        conn = _armed(engine, tenant_schema)
        session = OrmSession(bind=conn)
        try:
            barrier.wait()
            PostgresPatientPaymentRepository(session).add_write_off(
                patient_id=patient_owing_1000,
                amount_cents=_WRITE_OFF_CENTS,
                user_id=_CLINICIAN,
                reason="hardship",
                note=None,
            )
            with lock:
                results.append("written_off")
        except WriteOffExceedsBalanceError:
            session.rollback()
            with lock:
                results.append("refused")
        except BaseException as exc:
            session.rollback()
            with lock:
                errors.append(exc)
        finally:
            session.close()
            conn.close()

    threads = [threading.Thread(target=write_off) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"add_write_off raised under concurrency: {errors!r}"
    assert sorted(results) == ["refused", "written_off"]

    conn = _armed(engine, tenant_schema)
    try:
        ledger = PostgresPatientPaymentRepository(OrmSession(bind=conn)).list_charges(
            patient_owing_1000
        )
    finally:
        conn.close()
    written_off = [row.amount_cents for row in ledger if row.kind == "write_off"]
    assert written_off == [_WRITE_OFF_CENTS]
    assert sum(written_off) <= _BALANCE_CENTS
    assert patient_balance(ledger).balance_cents == _BALANCE_CENTS - _WRITE_OFF_CENTS
