# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A host whose records are all in place but which stays not active too long.

The service runs against the in-memory repository with a clock the test moves;
the routes are mounted the way ``test_practice_domains_routes.py`` mounts them,
to see what is audited. The Postgres repository and the reconciler's half are
proven in ``tests_integration/database/test_practice_domain_reconciler_db.py``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import require_active_subscription
from app.models.audit import AuditAction, ResourceType
from app.models.practice_domain import PracticeDomain, PracticeDomainApex, ServingState
from app.repositories.practice_domain import InMemoryPracticeDomainRepository
from app.routes import practice_domains
from app.services import practice_domain_service
from app.services.audit_service import get_audit_service
from app.services.practice_domain_dns import get_dns_lookup
from app.services.practice_domain_service import (
    STUCK_EVENT,
    PracticeDomainService,
    get_practice_domain_service,
)
from app.services.practice_domain_stuck import (
    DEFAULT_STUCK_MESSAGE,
    register_domain_stuck_message,
    reset_domain_stuck_message,
)
from app.settings import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models import User

PRACTICE_ID = "practice-1"
APEX = "ours.example"
HOST = f"portal.{APEX}"
TARGET = "sites.example.net"
AUTH = "abc.1"
TOKEN = "tok-test"
HOUR = timedelta(hours=1)
START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

Zone = dict[tuple[str, str], list[str] | None]


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, by: timedelta) -> None:
        self.now += by


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def log(self, action: Any, *_args: Any, **kwargs: Any) -> None:
        self.entries.append({"action": action, **kwargs})


@pytest.fixture(autouse=True)
def _no_registered_message() -> Iterator[None]:
    reset_domain_stuck_message()
    yield
    reset_domain_stuck_message()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def repo() -> InMemoryPracticeDomainRepository:
    repo = InMemoryPracticeDomainRepository()
    repo.put(
        PracticeDomain(
            domain=HOST,
            practice_id=PRACTICE_ID,
            purpose="portal",
            kind="vanity",
            status="verifying",
            is_primary=False,
            # Added long ago: the wait is never measured from here.
            created_at=START - timedelta(days=30),
            cert_auth_value=AUTH,
            cert_status="PROVISIONING",
        )
    )
    repo.put_apex(
        PracticeDomainApex(apex=APEX, practice_id=PRACTICE_ID, verify_token=TOKEN, created_at=START)
    )
    return repo


@pytest.fixture
def zone() -> Zone:
    return {}


@pytest.fixture
def service(repo: InMemoryPracticeDomainRepository, clock: Clock) -> PracticeDomainService:
    return PracticeDomainService(repo, cname_target=TARGET, stuck_after=HOUR, now=clock)


def _publish_all(zone: Zone) -> None:
    zone[(HOST, "CNAME")] = [TARGET]
    zone[(f"_acme-challenge.{HOST}", "CNAME")] = [f"{AUTH}.authorize.certificatemanager.goog"]
    zone[(f"_pablo-verify.{APEX}", "TXT")] = [f"pablo-verify={TOKEN}"]


def _check(service: PracticeDomainService, zone: Zone) -> Any:
    return service.check(PRACTICE_ID, lambda name, rdtype: zone.get((name, rdtype), []))


def _stored(repo: InMemoryPracticeDomainRepository) -> PracticeDomain:
    stored = repo.get(HOST)
    assert stored is not None
    return stored


class TestTheClock:
    def test_starts_when_every_record_is_first_found(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)

        _check(service, zone)

        assert _stored(repo).records_complete_at == START

    @pytest.mark.parametrize(
        "unpublished",
        [
            (HOST, "CNAME"),
            (f"_acme-challenge.{HOST}", "CNAME"),
            (f"_pablo-verify.{APEX}", "TXT"),
        ],
    )
    def test_does_not_start_while_a_record_is_missing(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        unpublished: tuple[str, str],
    ) -> None:
        _publish_all(zone)
        del zone[unpublished]

        _check(service, zone)

        assert _stored(repo).records_complete_at is None

    def test_does_not_start_before_a_certificate_is_requested(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
    ) -> None:
        """Until then the host's records are not all known."""
        repo.put(_with(_stored(repo), cert_auth_value=None, status="pending"))
        _publish_all(zone)

        _check(service, zone)

        assert _stored(repo).records_complete_at is None

    def test_is_kept_while_the_records_stay_in_place(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        clock.advance(timedelta(minutes=20))

        _check(service, zone)

        assert _stored(repo).records_complete_at == START

    def test_is_cleared_when_a_record_goes_missing(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        clock.advance(timedelta(minutes=20))
        del zone[(HOST, "CNAME")]

        _check(service, zone)

        assert _stored(repo).records_complete_at is None

    def test_is_cleared_when_a_record_goes_wrong(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        zone[(HOST, "CNAME")] = ["elsewhere.example.net"]

        _check(service, zone)

        assert _stored(repo).records_complete_at is None

    def test_no_answer_in_time_leaves_it_alone(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        clock.advance(timedelta(minutes=5))
        zone[(HOST, "CNAME")] = None

        _check(service, zone)

        assert _stored(repo).records_complete_at == START

    def test_dkim_records_are_not_the_hosts_to_wait_on(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
    ) -> None:
        apex = repo.get_apex(APEX)
        assert apex is not None
        apex.email_dkim_tokens = ["k1"]
        repo.put_apex(apex)
        _publish_all(zone)

        _check(service, zone)

        assert _stored(repo).records_complete_at == START

    def test_is_not_started_on_an_active_host(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
    ) -> None:
        repo.put(_with(_stored(repo), status="active"))
        _publish_all(zone)

        _check(service, zone)

        assert _stored(repo).records_complete_at is None


class TestStuck:
    def test_a_host_never_complete_is_never_stuck(
        self,
        service: PracticeDomainService,
        zone: Zone,
        clock: Clock,
    ) -> None:
        clock.advance(timedelta(days=3))

        (domain,) = _check(service, zone).domains

        assert domain.stuck is False
        assert domain.stuck_message is None

    def test_is_stuck_from_the_threshold_on(
        self,
        service: PracticeDomainService,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)

        clock.advance(HOUR - timedelta(seconds=1))
        (before,) = service.responses(PRACTICE_ID)
        clock.advance(timedelta(seconds=1))
        (at,) = service.responses(PRACTICE_ID)

        assert before.stuck is False
        assert before.stuck_message is None
        assert at.stuck is True
        assert at.stuck_message == DEFAULT_STUCK_MESSAGE

    def test_an_active_host_is_never_stuck(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
    ) -> None:
        repo.put(_with(_stored(repo), status="active", records_complete_at=START - HOUR * 5))

        (domain,) = service.responses(PRACTICE_ID)

        assert domain.stuck is False

    def test_the_answer_to_the_check_that_finds_it_says_so(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        repo.put(_with(_stored(repo), records_complete_at=START - HOUR))
        _publish_all(zone)

        (domain,) = _check(service, zone).domains

        assert domain.stuck is True

    def test_the_message_is_the_deployments_when_it_registered_one(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
    ) -> None:
        register_domain_stuck_message("Ask the front desk.")
        repo.put(_with(_stored(repo), records_complete_at=START - HOUR))

        (domain,) = service.responses(PRACTICE_ID)

        assert domain.stuck_message == "Ask the front desk."

    def test_the_default_message_names_nobody(self) -> None:
        assert "@" not in DEFAULT_STUCK_MESSAGE
        assert "pablo" not in DEFAULT_STUCK_MESSAGE.lower()

    def test_the_threshold_defaults_to_an_hour_and_reads_from_settings(self) -> None:
        assert Settings.model_fields["practice_domain_stuck_after_seconds"].default == 3600
        settings = SimpleNamespace(
            app_url="",
            backend_base_url="",
            portal_web_base_url="",
            companion_launch_url="",
            practice_domain_cname_target=TARGET,
            practice_domain_apex_ips="",
            practice_domain_dkim_cname_suffix="dkim.example.net",
            practice_domain_serving_project="",
            practice_domain_certificate_map="",
            practice_domain_url_map="",
            practice_domain_path_matcher="",
            practice_domain_stuck_after_seconds=120,
        )
        with patch.object(practice_domain_service, "get_settings", return_value=settings):
            service = get_practice_domain_service(InMemoryPracticeDomainRepository())

        assert service._stuck_after == timedelta(seconds=120)


class TestReport:
    def test_is_made_once_per_episode(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _publish_all(zone)
        assert _check(service, zone).stuck == []
        clock.advance(HOUR)

        with caplog.at_level(logging.WARNING, logger=practice_domain_service.__name__):
            first = _check(service, zone).stuck
            clock.advance(timedelta(minutes=30))
            again = _check(service, zone).stuck
            on_read = service.report_stuck(PRACTICE_ID)

        assert first == [HOST]
        assert again == []
        assert on_read == []
        assert _stored(repo).stuck_reported_at == START + HOUR
        (record,) = [r for r in caplog.records if getattr(r, "event", None) == STUCK_EVENT]
        # The host and the practice, nothing else.
        assert (record.domain, record.practice_id) == (HOST, PRACTICE_ID)  # type: ignore[attr-defined]  # logging extra
        assert record.getMessage() == f"{STUCK_EVENT} domain={HOST} practice_id={PRACTICE_ID}"

    def test_a_relapse_after_records_went_missing_is_a_new_episode(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        clock.advance(HOUR)
        assert _check(service, zone).stuck == [HOST]

        cname = zone.pop((HOST, "CNAME"))
        _check(service, zone)
        assert _stored(repo).stuck_reported_at is None
        zone[(HOST, "CNAME")] = cname
        clock.advance(timedelta(minutes=5))
        assert _check(service, zone).stuck == []
        clock.advance(HOUR)

        assert _check(service, zone).stuck == [HOST]

    def test_a_relapse_after_the_host_went_active_is_a_new_episode(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        _check(service, zone)
        clock.advance(HOUR)
        assert _check(service, zone).stuck == [HOST]

        _serve(repo, "verifying", "active")
        stored = _stored(repo)
        assert (stored.records_complete_at, stored.stuck_reported_at) == (None, None)
        assert service.responses(PRACTICE_ID)[0].stuck is False

        _serve(repo, "active", "error")
        _check(service, zone)
        clock.advance(HOUR)

        assert _check(service, zone).stuck == [HOST]

    def test_two_reporters_at_once_report_it_once(
        self,
        service: PracticeDomainService,
        repo: InMemoryPracticeDomainRepository,
    ) -> None:
        repo.put(_with(_stored(repo), records_complete_at=START - HOUR))
        assert repo.claim_stuck_report(HOST, PRACTICE_ID, START)

        assert service.report_stuck(PRACTICE_ID) == []


class TestRoutes:
    @pytest.fixture
    def audit(self) -> _RecordingAudit:
        return _RecordingAudit()

    @pytest.fixture
    def client(
        self,
        mock_user: User,
        service: PracticeDomainService,
        audit: _RecordingAudit,
        zone: Zone,
    ) -> Iterator[TestClient]:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(practice_domains.router)
        app.dependency_overrides[require_active_subscription] = lambda: mock_user
        app.dependency_overrides[get_practice_domain_service] = lambda: service
        app.dependency_overrides[get_audit_service] = lambda: audit
        app.dependency_overrides[get_dns_lookup] = lambda: (
            lambda name, rdtype: zone.get((name, rdtype), [])
        )
        session = MagicMock()
        session.get.return_value = SimpleNamespace(id=PRACTICE_ID, owner_email="test@example.com")
        with (
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=(PRACTICE_ID, "practice_1"),
            ),
            patch("app.db.get_db_session", return_value=session),
        ):
            yield TestClient(app)

    def _stuck_entries(self, audit: _RecordingAudit) -> list[dict[str, Any]]:
        return [e for e in audit.entries if e["action"] == AuditAction.PRACTICE_DOMAIN_STUCK]

    def test_a_check_that_finds_it_stuck_audits_it_once(
        self,
        client: TestClient,
        audit: _RecordingAudit,
        zone: Zone,
        clock: Clock,
    ) -> None:
        _publish_all(zone)
        client.post("/api/practice/domains/check")
        clock.advance(HOUR)

        (domain,) = client.post("/api/practice/domains/check").json()["domains"]
        client.post("/api/practice/domains/check")

        assert domain["stuck"] is True
        assert domain["stuck_message"] == DEFAULT_STUCK_MESSAGE
        assert self._stuck_entries(audit) == [
            {
                "action": AuditAction.PRACTICE_DOMAIN_STUCK,
                "resource_type": ResourceType.PRACTICE,
                "resource_id": PRACTICE_ID,
                "changes": {"domain": HOST},
            }
        ]

    def test_the_list_reports_it_when_it_sees_it_first(
        self,
        client: TestClient,
        repo: InMemoryPracticeDomainRepository,
        audit: _RecordingAudit,
    ) -> None:
        """The page shows the message on a plain read; the report goes out
        then too rather than waiting for the next check."""
        repo.put(_with(_stored(repo), records_complete_at=START - HOUR))

        (domain,) = client.get("/api/practice/domains").json()["domains"]
        client.get("/api/practice/domains")

        assert domain["stuck"] is True
        assert [e["changes"] for e in self._stuck_entries(audit)] == [{"domain": HOST}]

    def test_a_host_not_stuck_says_nothing(
        self, client: TestClient, audit: _RecordingAudit
    ) -> None:
        (domain,) = client.get("/api/practice/domains").json()["domains"]

        assert domain["stuck"] is False
        assert domain["stuck_message"] is None
        assert self._stuck_entries(audit) == []


def _with(domain: PracticeDomain, **changes: Any) -> PracticeDomain:
    for key, value in changes.items():
        setattr(domain, key, value)
    return domain


def _serve(repo: InMemoryPracticeDomainRepository, expected: str, status: str) -> None:
    """Write a status the way the reconciler does."""
    assert repo.record_serving(
        HOST,
        expected_status=expected,  # type: ignore[arg-type]  # a HostStatus literal
        state=replace(ServingState.of(_stored(repo)), status=status),  # type: ignore[arg-type]  # a HostStatus literal
    )
