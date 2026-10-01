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
from dataclasses import dataclass
from datetime import UTC, datetime
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
    POINTING_LAPSED,
    PracticeDomainReconciler,
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

    def service(self, repo: PracticeDomainRepository) -> PracticeDomainService:
        return PracticeDomainService(repo, cname_target=TARGET, deferred_removal=True)

    def sweep(self, *, store: PostgresReconcileStore | None = None) -> Any:
        return PracticeDomainReconciler(
            serving=self.serving,
            store=store or _OnlyThesePractices(self.practice.id),
            lookup=lambda name, rdtype: self.zone.get((name, rdtype), []),
            service_for=self.service,
            email=self.provisioner,
        ).sweep()

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


def test_a_certificate_that_failed_is_an_error_with_the_reason(harness: Harness) -> None:
    host = f"portal.{_apex()}"
    harness.add(host)
    harness.serving.certificate_state[host] = CertificateStatus("FAILED", f"{host}: CAA")

    harness.sweep()

    stored = harness.host(host)
    assert stored is not None
    assert stored.status == "error"
    assert stored.cert_status == "FAILED"
    assert stored.last_error == f"The certificate could not be issued ({host}: CAA)."


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
    assert (stored.status, stored.last_error) == ("verifying", None)


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


def _migration(direction: str, engine: Engine) -> None:
    spec = importlib.util.spec_from_file_location("practice_domain_serving_revision", _MIGRATION)
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
                    "AND column_name = 'last_error'"
                )
            ).scalar_one()
            check = conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'practice_domains_status_check'"
                )
            ).scalar_one()
        return bool(column), "removing" in check

    _migration("downgrade", engine)
    try:
        assert shape() == (False, False)
    finally:
        _migration("upgrade", engine)
    _migration("upgrade", engine)

    assert shape() == (True, True)
