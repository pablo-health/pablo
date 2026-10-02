# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The domain reconciler against real PostgreSQL.

Every sweep here runs the real store: hosts read from ``platform``, writes and
audit rows committed through a tenant session for a provisioned practice, read
back as its owner. The cloud is ``FakeDomainServing``; DNS is a table. What is
proved: each step a host takes (pending, verifying, active, error, removing and
gone), that a second sweep changes and creates nothing, that a lapse on a
served host leaves its routing in place and recovers by itself, that a removal
made mid-sweep is not overwritten, the email identity extension point, and the
revision going down and up.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.models import AuditLogRow
from app.db.tenant_session import tenant_db_session
from app.models.audit import AuditAction
from app.repositories.postgres.practice_domain import PostgresPracticeDomainRepository
from app.services.practice_domain_cloud import CertificateStatus, DomainServingError
from app.services.practice_domain_email import EmailIdentity
from app.services.practice_domain_reconcile_store import (
    ACTOR_COMPONENT,
    PostgresReconcileStore,
)
from app.services.practice_domain_reconciler import (
    CERTIFICATE_LAPSED,
    CERTIFICATE_PENDING,
    CERTIFICATE_RATE_LIMITED,
    POINTING_LAPSED,
    REISSUE_INTERVAL,
    PracticeDomainReconciler,
    challenge_record,
)
from app.services.practice_domain_service import PracticeDomainService
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from tests.practice_domain_serving_fake import FakeDomainServing

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.practice_domain import PracticeDomain
    from app.repositories.practice_domain import PracticeDomainRepository
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic_platform"
    / "versions"
    / "c5f1d7a93e28_practice_domain_serving.py"
)
_WAIT_MIGRATION = _MIGRATION.with_name("e3b8c41f6a52_practice_domain_records_complete.py")
TARGET = "sites.example.net"
ACTIVE = CertificateStatus("ACTIVE")
Zone = dict[tuple[str, str], list[str] | None]


@dataclass(frozen=True)
class Practice:
    id: str
    schema: str
    owner: str


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def practice(engine: Engine) -> Iterator[Practice]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    stamp = uuid.uuid4().hex[:8]
    made = Practice(
        id=f"domain-reconcile-{stamp}",
        schema=f"practice_domain_reconcile_{stamp}",
        owner=str(uuid.uuid4()),
    )
    create_practice_schema(engine, made.schema)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                " owner_user_id, product, status, is_active, created_at)"
                " VALUES (:i, 'Domain Reconcile Test', :s, 'owner@example.com', :o,"
                " 'pablo', 'active', true, now())"
            ),
            {"i": made.id, "s": made.schema, "o": made.owner},
        )
    yield made
    with engine.begin() as conn:
        for table in ("practice_domains", "practice_domain_apexes"):
            conn.execute(
                text(f"DELETE FROM platform.{table} WHERE practice_id = :i"),  # noqa: S608 — fixed table names
                {"i": made.id},
            )
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": made.id})
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{made.schema}" CASCADE'))


class _OnlyThesePractices(PostgresReconcileStore):
    """The real store, limited to this test's practices: other modules leave
    rows of their own in the shared platform schema."""

    def __init__(self, *practice_ids: str) -> None:
        self._ids = set(practice_ids)

    def all_hosts(self) -> list[PracticeDomain]:
        return [h for h in super().all_hosts() if h.practice_id in self._ids]


class _Provisioner:
    def __init__(self) -> None:
        self.identities: dict[str, EmailIdentity] = {}
        self.ensured: list[str] = []
        self.removed: list[str] = []

    def ensure(self, apex: str) -> EmailIdentity:
        self.ensured.append(apex)
        return self.identities.setdefault(apex, EmailIdentity("pending", ("k1", "k2", "k3")))

    def remove(self, apex: str) -> None:
        self.removed.append(apex)


@dataclass
class Harness:
    practice: Practice
    serving: FakeDomainServing
    zone: Zone
    provisioner: _Provisioner | None = None
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def service(self, repo: PracticeDomainRepository) -> PracticeDomainService:
        return PracticeDomainService(
            repo, cname_target=TARGET, deferred_removal=True, now=lambda: self.now
        )

    def sweep(self, *, store: PostgresReconcileStore | None = None) -> Any:
        return PracticeDomainReconciler(
            serving=self.serving,
            store=store or _OnlyThesePractices(self.practice.id),
            lookup=lambda name, rdtype: self.zone.get((name, rdtype), []),
            service_for=self.service,
            email=self.provisioner,
            now=lambda: self.now,
        ).sweep()

    def add_challenge(self, host: str) -> None:
        """Publish the host's ``_acme-challenge`` CNAME as it was shown."""
        name, value = challenge_record(host, self.serving.authorizations[host])
        self.zone[(name, "CNAME")] = [value]

    def add(self, host: str, purpose: str = "portal") -> None:
        with tenant_db_session(self.practice.schema, self.practice.owner) as session:
            self.service(PostgresPracticeDomainRepository(session)).add(
                self.practice.id,
                host,
                purpose,  # type: ignore[arg-type]  # a DomainPurpose literal
            )

    def remove(self, host: str) -> None:
        with tenant_db_session(self.practice.schema, self.practice.owner) as session:
            self.service(PostgresPracticeDomainRepository(session)).remove(self.practice.id, host)

    def host(self, host: str) -> PracticeDomain | None:
        with tenant_db_session(self.practice.schema, self.practice.owner) as session:
            return PostgresPracticeDomainRepository(session).get(host)

    def publish_ownership(self, apex: str) -> None:
        with tenant_db_session(self.practice.schema, self.practice.owner) as session:
            row = PostgresPracticeDomainRepository(session).get_apex(apex)
            assert row is not None
            self.zone[(f"_pablo-verify.{apex}", "TXT")] = [f"pablo-verify={row.verify_token}"]

    def point(self, host: str) -> None:
        self.zone[(host, "CNAME")] = [TARGET]

    def audit(self) -> list[dict[str, Any]]:
        with tenant_db_session(self.practice.schema, self.practice.owner) as session:
            rows = session.execute(
                select(AuditLogRow)
                .where(AuditLogRow.resource_id == self.practice.id)
                .order_by(AuditLogRow.timestamp)
            ).scalars()
            return [
                {
                    "action": r.action,
                    "changes": r.changes,
                    "actor_type": r.actor_type,
                    "actor_component": r.actor_component,
                    "user_id": r.user_id,
                }
                for r in rows
            ]


@pytest.fixture
def harness(practice: Practice) -> Harness:
    return Harness(practice=practice, serving=FakeDomainServing(), zone={})


def _apex() -> str:
    return f"r{uuid.uuid4().hex[:10]}.example"


def _ready(harness: Harness, host: str, apex: str) -> None:
    """Everything a host needs to be served: ownership, its record, a certificate."""
    harness.publish_ownership(apex)
    harness.point(host)
    harness.serving.certificate_state[host] = ACTIVE


def _statuses(harness: Harness) -> list[tuple[str, str]]:
    return [
        (e["changes"]["previous"], e["changes"]["status"])
        for e in harness.audit()
        if e["action"] == AuditAction.PRACTICE_DOMAIN_STATUS_CHANGED.value
    ]


# --- bringing a host up -------------------------------------------------------


def test_a_new_host_gets_its_records_and_waits(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)

    report = harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "verifying"
    assert stored.cert_auth_value == "test-auth.1"
    assert stored.cert_status == "PROVISIONING"
    assert report.changed == 1
    assert harness.serving.map_entries == set()
    assert harness.serving.host_rules == set()
    entry = harness.audit()[-1]
    assert entry == {
        "action": AuditAction.PRACTICE_DOMAIN_STATUS_CHANGED.value,
        "changes": {"domain": host, "status": "verifying", "previous": "pending"},
        "actor_type": "system",
        "actor_component": ACTOR_COMPONENT,
        "user_id": harness.practice.owner,
    }


def test_a_host_is_served_once_owned_pointed_and_certified(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.sweep()

    harness.point(host)
    harness.serving.certificate_state[host] = ACTIVE
    harness.sweep()
    assert harness.host(host).status == "verifying"  # type: ignore[union-attr]  # added above
    assert harness.serving.host_rules == set()

    harness.publish_ownership(apex)
    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "active"
    assert stored.verified_at is not None
    assert stored.last_error is None
    assert harness.serving.map_entries == {host}
    assert harness.serving.host_rules == {host}
    actions = [e["action"] for e in harness.audit()]
    assert AuditAction.PRACTICE_DOMAIN_OWNERSHIP_CONFIRMED.value in actions
    assert _statuses(harness) == [("pending", "verifying"), ("verifying", "active")]


def test_a_second_sweep_changes_and_creates_nothing(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()
    before = harness.host(host)
    audited = len(harness.audit())
    created = len(harness.serving.created())

    report = harness.sweep()

    assert report.changed == 0
    assert harness.host(host) == before
    assert len(harness.audit()) == audited
    assert len(harness.serving.created()) == created


# --- errors -------------------------------------------------------------------


def _attempt_failed(reason: str = "CONFIG") -> CertificateStatus:
    """A certificate behind a failed authorisation attempt, as the issuer
    leaves it when it looked before the record was added."""
    return CertificateStatus("PROVISIONING", f"{reason} CNAME_MISMATCH", reason)


def test_a_record_not_added_yet_waits_and_names_the_record(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.serving.certificate_state[host] = _attempt_failed()
    harness.sweep()  # stores the authorisation value the check then looks for

    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    name, value = challenge_record(host, "test-auth.1")
    assert (stored.status, stored.last_error) == (
        "verifying",
        f"Waiting for the record {name} CNAME {value}.",
    )
    assert ("recreate_certificate", host) not in harness.serving.calls


def test_a_certificate_still_authorising_without_its_record_waits_for_the_record(
    harness: Harness,
) -> None:
    """The issuer can sit in AUTHORIZING for a long time while the record is
    missing. That is waiting on the practice, never an error."""
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.sweep()

    for _ in range(3):
        harness.now += REISSUE_INTERVAL
        harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    name, value = challenge_record(host, "test-auth.1")
    assert (stored.status, stored.last_error) == (
        "verifying",
        f"Waiting for the record {name} CNAME {value}.",
    )
    assert ("recreate_certificate", host) not in harness.serving.calls
    assert ("verifying", "error") not in _statuses(harness)


def test_a_stuck_certificate_is_requested_again_once_its_record_is_in_place(
    harness: Harness,
) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.serving.certificate_state[host] = _attempt_failed()
    harness.sweep()
    harness.add_challenge(host)
    harness.publish_ownership(apex)
    harness.point(host)
    harness.serving.after_reissue[host] = ACTIVE

    harness.sweep()

    assert harness.serving.calls.count(("recreate_certificate", host)) == 1
    assert harness.serving.authorizations[host] == "test-auth.1"
    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "active"
    assert stored.cert_reissued_at == harness.now
    assert harness.serving.host_rules == {host}


def test_a_certificate_is_requested_again_at_most_once_per_interval(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.serving.certificate_state[host] = _attempt_failed()
    harness.serving.after_reissue[host] = _attempt_failed()
    harness.sweep()
    harness.add_challenge(host)

    harness.sweep()
    harness.now += REISSUE_INTERVAL - timedelta(minutes=1)
    harness.sweep()

    assert harness.serving.calls.count(("recreate_certificate", host)) == 1
    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "verifying"
    assert stored.last_error is not None
    assert stored.last_error.startswith("Waiting for the record _pablo-verify.")

    harness.now += timedelta(minutes=1)
    harness.sweep()
    assert harness.serving.calls.count(("recreate_certificate", host)) == 2


def test_two_runs_at_once_request_a_stuck_certificate_again_only_once(
    harness: Harness,
) -> None:
    """A second run starts while the first is between reading the host and
    acting on it. Both see the same stuck certificate; one re-requests it."""
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.serving.certificate_state[host] = _attempt_failed()
    harness.serving.after_reissue[host] = _attempt_failed()
    harness.sweep()
    harness.add_challenge(host)
    real_ensure = harness.serving.ensure_certificate
    overlapped: list[bool] = []

    def second_run_starts(name: str) -> Any:
        status = real_ensure(name)
        if not overlapped:
            overlapped.append(True)
            harness.sweep()
        return status

    harness.serving.ensure_certificate = second_run_starts  # type: ignore[method-assign]  # one-off interleaving
    harness.sweep()

    assert overlapped == [True]
    assert harness.serving.calls.count(("recreate_certificate", host)) == 1


def test_a_reissue_claim_is_won_once(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        repo = PostgresPracticeDomainRepository(session)
        assert repo.claim_reissue(host, last=None, at=harness.now) is True
        assert repo.claim_reissue(host, last=None, at=harness.now) is False
        later = harness.now + REISSUE_INTERVAL
        assert repo.claim_reissue(host, last=harness.now, at=later) is True


def test_a_sweep_says_which_hosts_are_left_and_who_they_wait_on(harness: Harness) -> None:
    on_practice, apex = f"portal.{_apex()}", _apex()
    on_issuer = f"portal.{apex}"
    harness.add(on_practice)
    harness.add(on_issuer)
    harness.sweep()
    harness.add_challenge(on_issuer)
    harness.publish_ownership(apex)
    harness.point(on_issuer)

    report = harness.sweep()

    assert report.in_progress == {on_practice, on_issuer}
    assert report.awaiting_practice == {on_practice}
    assert harness.host(on_issuer).last_error == CERTIFICATE_PENDING  # type: ignore[union-attr]  # added above
    assert PostgresReconcileStore().any_in_progress() is True


def test_a_rate_limited_certificate_is_left_to_wait(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.serving.certificate_state[host] = _attempt_failed("RATE_LIMITED")
    harness.sweep()
    harness.add_challenge(host)
    harness.publish_ownership(apex)
    harness.point(host)

    harness.sweep()

    assert ("recreate_certificate", host) not in harness.serving.calls
    stored = harness.host(host)
    assert stored is not None
    assert (stored.status, stored.last_error) == ("verifying", CERTIFICATE_RATE_LIMITED)


def test_a_caa_refusal_is_an_error_the_practice_must_fix(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.serving.certificate_state[host] = CertificateStatus(
        "FAILED", f"{host}: CAA forbidden", "CAA"
    )

    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "error"
    assert stored.cert_status == "FAILED"
    assert stored.last_error == (
        f"The domain's CAA records do not allow this certificate ({host}: CAA forbidden)."
    )


def test_a_refused_request_is_an_error_and_a_busy_one_waits(harness: Harness) -> None:
    refused, busy = f"portal.{_apex()}", f"portal.{_apex()}"
    harness.add(refused)
    harness.serving.failures["ensure_certificate"] = DomainServingError(
        "Certificate: InvalidArgument: bad hostname", transient=False
    )
    harness.sweep()

    stored = harness.host(refused)
    assert stored is not None
    assert (stored.status, stored.last_error) == (
        "error",
        "Certificate: InvalidArgument: bad hostname",
    )

    harness.add(busy)
    harness.serving.failures["ensure_certificate"] = DomainServingError(
        "Certificate: ServiceUnavailable", transient=True
    )
    harness.sweep()
    assert harness.host(busy).status == "pending"  # type: ignore[union-attr]  # added above

    # Once the cloud takes the request, the refused host starts over.
    del harness.serving.failures["ensure_certificate"]
    harness.sweep()
    stored = harness.host(refused)
    assert stored is not None
    assert stored.status == "verifying"
    assert stored.last_error is not None
    assert stored.last_error.startswith("Waiting for")


def test_a_served_host_whose_record_lapses_keeps_its_routing_and_recovers(
    harness: Harness,
) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()

    harness.zone[(host, "CNAME")] = ["somewhere.else.example"]
    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert (stored.status, stored.last_error) == ("error", POINTING_LAPSED)
    assert harness.serving.host_rules == {host}
    assert ("remove_host_rule", host) not in harness.serving.calls

    harness.point(host)
    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert (stored.status, stored.last_error) == ("active", None)
    assert _statuses(harness)[-2:] == [("active", "error"), ("error", "active")]


def test_a_served_host_whose_certificate_lapses_is_an_error(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()

    harness.serving.certificate_state[host] = CertificateStatus("PROVISIONING")
    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert (stored.status, stored.last_error) == ("error", CERTIFICATE_LAPSED)
    assert harness.serving.host_rules == {host}


def test_no_answer_in_time_changes_nothing_on_a_served_host(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()

    harness.zone[(host, "CNAME")] = None
    report = harness.sweep()

    assert harness.host(host).status == "active"  # type: ignore[union-attr]  # added above
    assert report.changed == 0


def test_a_refusal_on_a_served_host_leaves_it_active(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()

    harness.serving.failures["ensure_host_rule"] = DomainServingError(
        "URL map: PermissionDenied", transient=False
    )
    harness.sweep()

    assert harness.host(host).status == "active"  # type: ignore[union-attr]  # added above


# --- removal ------------------------------------------------------------------


def test_a_removed_host_is_taken_down_in_order_then_deleted(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.provisioner = _Provisioner()
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()
    assert harness.serving.host_rules == {host}

    harness.remove(host)
    assert harness.host(host).status == "removing"  # type: ignore[union-attr]  # just marked
    harness.serving.calls.clear()
    report = harness.sweep()

    assert harness.serving.calls == [
        ("remove_host_rule", host),
        ("remove_map_entry", host),
        ("remove_certificate", host),
        ("remove_dns_authorization", host),
    ]
    assert report.released == 1
    assert harness.host(host) is None
    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        assert PostgresPracticeDomainRepository(session).get_apex(apex) is None
    assert harness.provisioner.removed == [apex]
    assert harness.audit()[-1]["action"] == AuditAction.PRACTICE_DOMAIN_RELEASED.value
    assert harness.audit()[-1]["changes"] == {"domain": host}


def test_a_domain_stays_while_another_host_under_it_does(harness: Harness) -> None:
    apex = _apex()
    harness.add(f"portal.{apex}")
    harness.add(f"book.{apex}")
    harness.sweep()

    harness.remove(f"portal.{apex}")
    harness.sweep()

    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        assert PostgresPracticeDomainRepository(session).get_apex(apex) is not None
    assert harness.host(f"book.{apex}") is not None


def test_a_take_down_the_cloud_refuses_waits_with_the_reason(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.sweep()
    harness.remove(host)
    harness.serving.failures["remove_certificate"] = DomainServingError(
        "Certificate: FailedPrecondition: in use", transient=False
    )

    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert (stored.status, stored.last_error) == (
        "removing",
        "Certificate: FailedPrecondition: in use",
    )


def test_a_removal_made_during_a_sweep_is_not_overwritten(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    real_ensure = harness.serving.ensure_host_rule

    def removed_meanwhile(name: str) -> None:
        harness.remove(name)
        real_ensure(name)

    harness.serving.ensure_host_rule = removed_meanwhile  # type: ignore[method-assign]  # one-off interleaving
    harness.sweep()

    assert harness.host(host).status == "removing"  # type: ignore[union-attr]  # added above


def test_a_host_whose_practice_is_gone_is_still_taken_down(
    harness: Harness, engine: Engine
) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.remove(host)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM platform.practices WHERE id = :i"), {"i": harness.practice.id}
        )

    report = harness.sweep()

    assert report.released == 1
    assert harness.host(host) is None


def test_a_deactivated_practices_hosts_stop_being_served(harness: Harness, engine: Engine) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    _ready(harness, host, apex)
    harness.sweep()
    assert harness.serving.host_rules == {host}
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platform.practices SET is_active = false WHERE id = :i"),
            {"i": harness.practice.id},
        )

    report = harness.sweep()

    assert report.released == 1
    assert harness.host(host) is None
    assert harness.serving.host_rules == set()
    assert harness.serving.certificates == set()
    retired = [
        e["changes"]
        for e in harness.audit()
        if e["action"] == AuditAction.PRACTICE_DOMAIN_STATUS_CHANGED.value
    ][-1]
    assert retired == {
        "domain": host,
        "status": "removing",
        "previous": "active",
        "reason": "practice_inactive",
    }


def test_an_offboarded_practices_hosts_stop_being_served(harness: Harness, engine: Engine) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.sweep()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE platform.practices SET deleted_at = now() WHERE id = :i"),
            {"i": harness.practice.id},
        )

    report = harness.sweep()

    assert report.released == 1
    assert report.failed_practices == 0
    assert harness.host(host) is None


# --- email identities ---------------------------------------------------------


def test_an_owned_domain_gets_an_email_identity_and_an_unowned_one_does_not(
    harness: Harness,
) -> None:
    owned, unowned = _apex(), _apex()
    harness.provisioner = _Provisioner()
    harness.add(f"portal.{owned}")
    harness.add(f"portal.{unowned}")
    harness.publish_ownership(owned)

    harness.sweep()

    assert harness.provisioner.ensured == [owned]
    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        repo = PostgresPracticeDomainRepository(session)
        stored = repo.get_apex(owned)
        assert stored is not None
        assert (stored.email_identity_status, stored.email_dkim_tokens) == (
            "pending",
            ["k1", "k2", "k3"],
        )
        assert repo.get_apex(unowned).email_identity_status is None  # type: ignore[union-attr]  # added above
    changed = [
        e["changes"]
        for e in harness.audit()
        if e["action"] == AuditAction.PRACTICE_DOMAIN_EMAIL_IDENTITY_CHANGED.value
    ]
    assert changed == [{"domain": owned, "status": "pending"}]

    harness.provisioner.identities[owned] = EmailIdentity("verified", ("k1", "k2", "k3"))
    harness.sweep()
    harness.sweep()
    changed = [
        e["changes"]
        for e in harness.audit()
        if e["action"] == AuditAction.PRACTICE_DOMAIN_EMAIL_IDENTITY_CHANGED.value
    ]
    assert changed[-1] == {"domain": owned, "status": "verified"}
    assert len(changed) == 2


def test_without_a_provisioner_no_email_fields_are_written(harness: Harness) -> None:
    apex = _apex()
    harness.add(f"portal.{apex}")
    harness.publish_ownership(apex)

    harness.sweep()

    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        stored = PostgresPracticeDomainRepository(session).get_apex(apex)
    assert stored is not None
    assert (stored.email_identity_status, stored.email_dkim_tokens) == (None, None)


# --- the schema ---------------------------------------------------------------


# --- a host that takes too long -------------------------------------------------


def _all_records_in_place(harness: Harness, host: str, apex: str) -> None:
    """Everything the practice can do: ownership, the host's record and its
    certificate's authorisation. The certificate is left to the issuer."""
    harness.publish_ownership(apex)
    harness.point(host)
    harness.add_challenge(host)


def _stuck_reports(harness: Harness) -> list[dict[str, Any]]:
    return [
        e["changes"]
        for e in harness.audit()
        if e["action"] == AuditAction.PRACTICE_DOMAIN_STUCK.value
    ]


def test_a_host_waiting_an_hour_on_complete_records_is_reported_once(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.sweep()  # requests the authorisation the challenge record names
    _all_records_in_place(harness, host, apex)
    harness.serving.certificate_state[host] = CertificateStatus("PROVISIONING")

    harness.sweep()
    started = harness.host(host)
    assert started is not None
    assert started.records_complete_at == harness.now
    harness.now += timedelta(minutes=59)
    harness.sweep()
    assert _stuck_reports(harness) == []

    harness.now += timedelta(minutes=1)
    harness.sweep()
    harness.now += timedelta(minutes=2)
    harness.sweep()

    assert _stuck_reports(harness) == [{"domain": host}]
    reported = harness.host(host)
    assert reported is not None
    assert reported.status == "verifying"
    assert reported.stuck_reported_at == started.records_complete_at + timedelta(hours=1)  # type: ignore[operator]  # set above
    entry = next(
        e for e in harness.audit() if e["action"] == AuditAction.PRACTICE_DOMAIN_STUCK.value
    )
    assert (entry["actor_type"], entry["actor_component"]) == ("system", ACTOR_COMPONENT)


def test_a_reported_host_that_goes_active_starts_afresh(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.sweep()
    _all_records_in_place(harness, host, apex)
    harness.sweep()
    harness.now += timedelta(hours=1)
    harness.sweep()
    assert _stuck_reports(harness) == [{"domain": host}]

    # Checking went on; the issuer came through.
    harness.serving.certificate_state[host] = ACTIVE
    harness.sweep()

    served = harness.host(host)
    assert served is not None
    assert served.status == "active"
    assert (served.records_complete_at, served.stuck_reported_at) == (None, None)

    # A lapse with every record still in place is a new wait, and a new report.
    harness.serving.certificate_state[host] = CertificateStatus("PROVISIONING")
    harness.sweep()
    harness.sweep()
    harness.now += timedelta(hours=1)
    harness.sweep()

    lapsed = harness.host(host)
    assert lapsed is not None
    assert lapsed.status == "error"
    assert _stuck_reports(harness) == [{"domain": host}, {"domain": host}]


def test_a_missing_record_clears_the_wait(harness: Harness) -> None:
    apex = _apex()
    host = f"portal.{apex}"
    harness.add(host)
    harness.sweep()
    _all_records_in_place(harness, host, apex)
    harness.sweep()
    harness.now += timedelta(hours=1)
    harness.sweep()

    del harness.zone[(host, "CNAME")]
    harness.sweep()

    cleared = harness.host(host)
    assert cleared is not None
    assert (cleared.records_complete_at, cleared.stuck_reported_at) == (None, None)


def test_the_wait_columns_are_kept_and_cleared_in_postgres(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    at = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        repo = PostgresPracticeDomainRepository(session)
        assert repo.mark_records_complete(host, harness.practice.id, at)
        assert not repo.mark_records_complete(host, harness.practice.id, at + timedelta(hours=1))
        assert not repo.mark_records_complete(host, "another-practice", at)
        assert repo.claim_stuck_report(host, harness.practice.id, at + timedelta(hours=1))
        assert not repo.claim_stuck_report(host, harness.practice.id, at + timedelta(hours=2))
    stored = harness.host(host)
    assert stored is not None
    assert (stored.records_complete_at, stored.stuck_reported_at) == (at, at + timedelta(hours=1))

    with tenant_db_session(harness.practice.schema, harness.practice.owner) as session:
        repo = PostgresPracticeDomainRepository(session)
        assert repo.clear_records_complete(host, harness.practice.id)
        assert not repo.clear_records_complete(host, harness.practice.id)
    cleared = harness.host(host)
    assert cleared is not None
    assert (cleared.records_complete_at, cleared.stuck_reported_at) == (None, None)


def test_the_wait_revision_goes_down_and_comes_up_twice(engine: Engine) -> None:
    def columns() -> int:
        with engine.begin() as conn:
            return int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = 'platform' AND table_name = 'practice_domains' "
                        "AND column_name IN ('records_complete_at', 'stuck_reported_at')"
                    )
                ).scalar_one()
            )

    _migration("downgrade", engine, _WAIT_MIGRATION)
    try:
        assert columns() == 0
    finally:
        _migration("upgrade", engine, _WAIT_MIGRATION)
    _migration("upgrade", engine, _WAIT_MIGRATION)

    assert columns() == 2


def test_an_unknown_status_is_still_refused(engine: Engine) -> None:
    session = sessionmaker(bind=engine)()
    try:
        with pytest.raises(IntegrityError, match="practice_domains_status_check"):
            session.execute(
                text(
                    "INSERT INTO platform.practice_domains "
                    "(domain, practice_id, kind, status, created_at) "
                    "VALUES (:d, 'p', 'vanity', 'retired', :t)"
                ),
                {"d": f"x.{_apex()}", "t": datetime.now(UTC)},
            )
    finally:
        session.rollback()
        session.close()


def _migration(direction: str, engine: Engine, path: Path = _MIGRATION) -> None:
    spec = importlib.util.spec_from_file_location(f"revision_{path.stem}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(module, direction)()


def test_the_revision_goes_down_and_comes_up_twice(engine: Engine) -> None:
    def shape() -> tuple[bool, bool]:
        with engine.begin() as conn:
            column = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'platform' AND table_name = 'practice_domains' "
                    "AND column_name IN ('last_error', 'cert_reissued_at')"
                )
            ).scalar_one()
            check = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'practice_domains_status_check'"
                )
            ).scalar_one()
        return column == 2, "removing" in check

    _migration("downgrade", engine)
    try:
        assert shape() == (False, False)
    finally:
        _migration("upgrade", engine)
    _migration("upgrade", engine)

    assert shape() == (True, True)
