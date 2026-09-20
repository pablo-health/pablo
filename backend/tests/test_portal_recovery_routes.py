# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""HTTP tests for account recovery — the one route on this surface that
takes a stranger's word for who they are asking about.

The real handler on a fresh app, with the practice directory and the
tenant work swapped for in-memory doubles. What these are FOR, in order of
how much they would hurt to get wrong:

* **One answer, always.** A known address with live access, an address
  nobody here has, a patient whose access was withdrawn, a practice that
  does not exist — all four answer with a byte-identical 202. Anything else
  and this endpoint tells a stranger whether somebody is in treatment.
* **Only a match mints.** The other three send nothing and write no
  patient-scoped audit row — because a row scoped to a patient id says that
  patient exists, which would move the oracle out of the response and into
  the audit log.
* **Only an ACTIVE grant mints.** A clinician's revoke is not undone by the
  person it was used on.
* **Two charts with one address mint nothing**, rather than a guess about
  which person is asking.
* **The windows are enforced**, per address and per practice.

The database-backed gateway is proven separately against a freshly
provisioned practice schema.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from app.api_errors import register_exception_handlers
from app.portal.delivery import CapturingInviteDelivery, DeliveryNotConfigured, FakeSmsGateway
from app.portal.factory import get_invite_delivery, get_sms_gateway
from app.portal.recovery import router
from app.portal.recovery_gateway import RecoveryTarget, RecoveryWork, get_recovery_gateway
from app.portal.store import (
    InMemoryPortalAuthStore,
    InMemoryPortalSessionStore,
    InviteChallenge,
    PortalSessionRecord,
)
from app.rate_limit import require_portal_recover_rate_limit, reset_portal_limiters
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

TENANT = "practice_abc123"
SLUG = "meadowlark"
UNKNOWN_SLUG = "no-such-practice"
SIGNING_KEY = "recovery-route-test-key-not-a-real-secret"
PORTAL_ORIGIN = "https://portal.example.test"

NOW = 1_800_000_000

# Four charts, one per shape the route has to tell apart WITHOUT the caller
# being able to.
ACTIVE = RecoveryTarget("patient-active", "ada@example.test", "+15005550006")
REVOKED = RecoveryTarget("patient-revoked", "revoked@example.test", "+15005550007")
NEVER_INVITED = RecoveryTarget("patient-new", "new@example.test", "+15005550008")
NO_PHONE = RecoveryTarget("patient-no-phone", "nophone@example.test", None)
SHARED = "family@example.test"
UNKNOWN_EMAIL = "stranger@example.test"

_BY_EMAIL: dict[str, RecoveryTarget] = {
    ACTIVE.email or "": ACTIVE,
    REVOKED.email or "": REVOKED,
    NEVER_INVITED.email or "": NEVER_INVITED,
    NO_PHONE.email or "": NO_PHONE,
}


class _FakeGateway:
    """The recovery gateway over in-memory stores.

    Records which practice it was asked for and whether it was committed,
    because "the schema came from the directory" and "nothing was written
    on a miss" are properties worth asserting rather than assuming.
    """

    def __init__(
        self, challenges: InMemoryPortalAuthStore, sessions: InMemoryPortalSessionStore
    ) -> None:
        self.challenges = challenges
        self.sessions = sessions
        self.opened: list[str] = []
        self.commits = 0
        self.recorded: list[tuple[str, str]] = []

    def resolve(self, slug: str) -> str | None:
        return TENANT if slug == SLUG else None

    @contextmanager
    def open(self, schema: str, request: Any) -> Iterator[RecoveryWork]:
        self.opened.append(schema)

        def _find(email: str) -> RecoveryTarget | None:
            if email.lower() == SHARED:
                # Two charts share the address: the real query takes two
                # rows and refuses on two.
                return None
            return _BY_EMAIL.get(email.lower())

        def _commit() -> None:
            self.commits += 1

        def _record(patient_id: str, invite_jti: str) -> None:
            self.recorded.append((patient_id, invite_jti))

        yield RecoveryWork(
            find_patient=_find,
            challenges=self.challenges,
            sessions=self.sessions,
            record_request=_record,
            commit=_commit,
        )


@pytest.fixture(autouse=True)
def _portal_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("PORTAL_TOKEN_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("PORTAL_WEB_BASE_URL", PORTAL_ORIGIN)
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_rate_limits() -> Iterator[None]:
    """The limiters are module-level singletons; a test that spends a window
    must not charge it to the next one."""
    reset_portal_limiters()
    yield
    reset_portal_limiters()


@pytest.fixture
def challenges() -> InMemoryPortalAuthStore:
    store = InMemoryPortalAuthStore()
    # An invitation that expired unredeemed: still unconsumed, so the grant
    # stands. This is the most ordinary reason somebody asks for a new link.
    store.put_challenge(
        InviteChallenge(
            jti="expired-but-unspent",
            patient_id=ACTIVE.patient_id,
            tenant=TENANT,
            otp_hash="x",
            expires_at=NOW - 60,
        )
    )
    # The withdrawn patient's invitation was burned by the kill switch.
    store.put_challenge(
        InviteChallenge(
            jti="burned",
            patient_id=REVOKED.patient_id,
            tenant=TENANT,
            otp_hash="x",
            expires_at=NOW + 900,
            consumed=True,
        )
    )
    return store


@pytest.fixture
def sessions() -> InMemoryPortalSessionStore:
    store = InMemoryPortalSessionStore()
    # The withdrawn patient's sessions were all revoked by the kill switch.
    store.record(
        PortalSessionRecord(
            jti="revoked-session",
            patient_id=REVOKED.patient_id,
            issued_at=NOW,
            expires_at=NOW + 3600,
            chain_started_at=NOW,
            revoked_at=NOW + 10,
        )
    )
    return store


@pytest.fixture
def gateway(
    challenges: InMemoryPortalAuthStore, sessions: InMemoryPortalSessionStore
) -> _FakeGateway:
    return _FakeGateway(challenges, sessions)


@pytest.fixture
def sms() -> FakeSmsGateway:
    return FakeSmsGateway()


@pytest.fixture
def delivery() -> CapturingInviteDelivery:
    return CapturingInviteDelivery()


@pytest.fixture
def app(gateway: _FakeGateway, sms: FakeSmsGateway, delivery: CapturingInviteDelivery) -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)
    application.include_router(router)
    application.dependency_overrides[get_recovery_gateway] = lambda: gateway
    application.dependency_overrides[get_invite_delivery] = lambda: delivery
    application.dependency_overrides[get_sms_gateway] = lambda: sms
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _miss_records(caplog: pytest.LogCaptureFixture) -> list[Any]:
    """The route's own "matched nothing" lines, and nothing else's.

    Filtered by logger AND by the field, so an unrelated line from httpx or
    a future log statement here cannot make the assertions pass or fail for
    the wrong reason.
    """
    return [
        record
        for record in caplog.records
        if record.name == "app.portal.recovery" and hasattr(record, "email_handle")
    ]


def _recover(client: TestClient, email: str, *, slug: str = SLUG) -> Any:
    return client.post(f"/api/portal/practices/{slug}/recover", json={"email": email})


class TestTheAnswerIsAlwaysTheSame:
    """The property the whole route exists to hold.

    Whether an address is on a therapy practice's patient list is not
    something a stranger gets to test for, so every shape answers
    identically — status, headers that matter, and body.
    """

    @pytest.mark.parametrize(
        ("email", "slug"),
        [
            (ACTIVE.email, SLUG),
            (UNKNOWN_EMAIL, SLUG),
            (REVOKED.email, SLUG),
            (NEVER_INVITED.email, SLUG),
            (NO_PHONE.email, SLUG),
            (SHARED, SLUG),
            (ACTIVE.email, UNKNOWN_SLUG),
        ],
        ids=[
            "match",
            "no-such-address",
            "access-withdrawn",
            "never-invited",
            "no-phone-on-chart",
            "two-charts-one-address",
            "no-such-practice",
        ],
    )
    def test_every_shape_answers_202(self, client: TestClient, email: str, slug: str) -> None:
        response = _recover(client, email, slug=slug)

        assert response.status_code == 202

    def test_the_bodies_are_byte_identical(self, client: TestClient) -> None:
        bodies = {
            _recover(client, ACTIVE.email or "").content,
            _recover(client, UNKNOWN_EMAIL).content,
            _recover(client, REVOKED.email or "").content,
            _recover(client, ACTIVE.email or "", slug=UNKNOWN_SLUG).content,
        }

        assert len(bodies) == 1

    def test_a_malformed_address_is_the_one_different_answer(self, client: TestClient) -> None:
        """422 on shape, and that is safe.

        It says something about the request the caller sent and nothing
        about whether any patient matches it.
        """
        assert _recover(client, "not-an-address").status_code == 422


class TestOnlyAMatchMints:
    def test_a_known_patient_with_an_active_grant_is_sent_a_link(
        self,
        client: TestClient,
        delivery: CapturingInviteDelivery,
        sms: FakeSmsGateway,
    ) -> None:
        _recover(client, ACTIVE.email or "")

        assert len(delivery.sent) == 1
        assert len(sms.sent) == 1

    def test_the_link_goes_to_the_address_on_the_chart(
        self, client: TestClient, delivery: CapturingInviteDelivery
    ) -> None:
        """Not to the string the caller typed, even though they match.

        Sending to the stored value means the recipient is the practice's
        record of this person rather than anything a stranger supplied.
        """
        _recover(client, (ACTIVE.email or "").upper())

        assert delivery.sent[0].to_email == ACTIVE.email

    def test_the_code_goes_to_the_phone_on_the_chart(
        self, client: TestClient, sms: FakeSmsGateway
    ) -> None:
        """The second factor, and the reason a mailed link is not enough."""
        _recover(client, ACTIVE.email or "")

        assert sms.sent[0].to == ACTIVE.phone

    def test_the_response_carries_no_token_and_no_link(self, client: TestClient) -> None:
        response = _recover(client, ACTIVE.email or "")

        assert response.content == b""

    @pytest.mark.parametrize(
        "email",
        [UNKNOWN_EMAIL, REVOKED.email, NEVER_INVITED.email, NO_PHONE.email, SHARED],
        ids=["no-such-address", "withdrawn", "never-invited", "no-phone", "shared-address"],
    )
    def test_every_other_shape_sends_nothing(
        self,
        client: TestClient,
        delivery: CapturingInviteDelivery,
        sms: FakeSmsGateway,
        email: str,
    ) -> None:
        _recover(client, email)

        assert delivery.sent == []
        assert sms.sent == []

    def test_an_unknown_practice_never_opens_a_tenant_session(
        self, client: TestClient, gateway: _FakeGateway
    ) -> None:
        """The refusal happens in the directory, before any schema is entered."""
        _recover(client, ACTIVE.email or "", slug=UNKNOWN_SLUG)

        assert gateway.opened == []

    def test_the_schema_comes_from_the_directory(
        self, client: TestClient, gateway: _FakeGateway
    ) -> None:
        _recover(client, ACTIVE.email or "")

        assert gateway.opened == [TENANT]


class TestOnlyAnActiveGrantMints:
    def test_an_expired_unredeemed_invitation_still_counts_as_a_grant(
        self, client: TestClient, delivery: CapturingInviteDelivery
    ) -> None:
        """The case recovery exists for.

        The invitation timed out before they got to it. Nothing was
        withdrawn, so a new link is exactly right.
        """
        _recover(client, ACTIVE.email or "")

        assert len(delivery.sent) == 1

    def test_a_withdrawn_patient_gets_nothing(
        self, client: TestClient, delivery: CapturingInviteDelivery
    ) -> None:
        """What makes the clinician's kill switch mean something.

        The revoke burned every invitation and revoked every session, so
        there is no grant left for this route to find — and only a clinician
        re-invite brings one back.
        """
        _recover(client, REVOKED.email or "")

        assert delivery.sent == []

    def test_a_patient_who_was_never_invited_gets_nothing(
        self, client: TestClient, delivery: CapturingInviteDelivery
    ) -> None:
        """A chart is not a portal account.

        Otherwise anyone who knew a practice's patient list could have the
        portal open itself for them.
        """
        _recover(client, NEVER_INVITED.email or "")

        assert delivery.sent == []

    def test_a_lapsed_session_still_counts_as_a_grant(
        self,
        client: TestClient,
        sessions: InMemoryPortalSessionStore,
        delivery: CapturingInviteDelivery,
    ) -> None:
        """Expiry is not withdrawal.

        Somebody who signed in months ago and let the session lapse still
        has access as far as the practice is concerned.
        """
        sessions.record(
            PortalSessionRecord(
                jti="long-lapsed",
                patient_id=NEVER_INVITED.patient_id,
                issued_at=NOW - 100_000,
                expires_at=NOW - 90_000,
                chain_started_at=NOW - 100_000,
            )
        )

        _recover(client, NEVER_INVITED.email or "")

        assert len(delivery.sent) == 1


class TestTheAuditRowIsNotAnOracle:
    def test_a_match_is_recorded(self, client: TestClient, gateway: _FakeGateway) -> None:
        _recover(client, ACTIVE.email or "")

        assert len(gateway.recorded) == 1
        patient_id, invite_jti = gateway.recorded[0]
        assert patient_id == ACTIVE.patient_id
        assert invite_jti

    @pytest.mark.parametrize(
        "email",
        [UNKNOWN_EMAIL, REVOKED.email, NEVER_INVITED.email, SHARED],
        ids=["no-such-address", "withdrawn", "never-invited", "shared-address"],
    )
    def test_a_miss_writes_no_patient_scoped_row(
        self, client: TestClient, gateway: _FakeGateway, email: str
    ) -> None:
        """A row scoped to a patient id says that patient exists.

        Auditing every attempt would move the enumeration oracle out of the
        response and into the audit log, where it is just as readable and
        harder to notice.
        """
        _recover(client, email)

        assert gateway.recorded == []

    def test_a_miss_commits_nothing(self, client: TestClient, gateway: _FakeGateway) -> None:
        _recover(client, UNKNOWN_EMAIL)

        assert gateway.commits == 0

    def test_a_match_commits_the_invitation_and_the_row_together(
        self, client: TestClient, gateway: _FakeGateway
    ) -> None:
        _recover(client, ACTIVE.email or "")

        assert gateway.commits == 1

    def test_a_miss_logs_only_a_keyed_digest(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Enough to recognise a sweep, not enough to confirm a guess.

        An unkeyed hash of an email address is not anonymous — the space is
        small enough to enumerate — so the digest is keyed on the
        deployment's own signing key and truncated.
        """
        with caplog.at_level("INFO", logger="app.portal.recovery"):
            _recover(client, UNKNOWN_EMAIL)

        misses = _miss_records(caplog)
        assert UNKNOWN_EMAIL not in caplog.text
        assert len(misses) == 1
        assert misses[0].email_handle
        assert misses[0].email_handle not in UNKNOWN_EMAIL
        assert misses[0].practice_slug == SLUG

    def test_the_same_address_logs_the_same_handle(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Which is what makes a burst recognisable as one."""
        with caplog.at_level("INFO", logger="app.portal.recovery"):
            _recover(client, UNKNOWN_EMAIL)
            _recover(client, UNKNOWN_EMAIL.upper())

        handles = [record.email_handle for record in _miss_records(caplog)]
        assert len(handles) == 2
        assert handles[0] == handles[1]

    def test_two_addresses_log_different_handles(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("INFO", logger="app.portal.recovery"):
            _recover(client, UNKNOWN_EMAIL)
            _recover(client, "someone-else@example.test")

        handles = [record.email_handle for record in _miss_records(caplog)]
        assert handles[0] != handles[1]


class TestTheWindows:
    def test_the_per_address_window_closes(self, client: TestClient) -> None:
        """Five an hour. The uniform answer stops the response being an
        oracle; it does nothing about volume, and this is what does."""
        codes = [_recover(client, UNKNOWN_EMAIL).status_code for _ in range(7)]

        assert codes[:5] == [202] * 5
        assert codes[5:] == [429, 429]

    def test_the_per_practice_window_closes_across_addresses(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """The bound that actually matters.

        A caller with a pool of source addresses walks straight through a
        per-IP limit, and what they would be walking through is one
        practice's patient list. So the practice gets a budget of its own,
        twenty an hour — a real practice's patients ask for a new link a
        handful of times a day between them, and a sweep does not look like
        that.

        The per-address window is overridden away so that what closes here
        is unambiguously the per-practice one.
        """
        app.dependency_overrides[require_portal_recover_rate_limit] = lambda: None

        codes = [_recover(client, f"sweep-{n}@example.test").status_code for n in range(22)]

        assert codes.count(202) == 20
        assert codes.count(429) == 2

    def test_one_practices_window_does_not_close_anothers(self, client: TestClient) -> None:
        """Keyed on the slug, so a sweep of one practice cannot deny another."""
        for _ in range(5):
            _recover(client, UNKNOWN_EMAIL)

        assert _recover(client, UNKNOWN_EMAIL).status_code == 429


class TestWhenDeliveryIsNotWired:
    def test_a_deployment_with_no_channels_still_answers_202(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """It cannot say 503 the way the clinician's invite route does.

        The clinician is inside the practice and may be told the deployment
        is misconfigured. Telling this caller would tell them their address
        matched.
        """
        app.dependency_overrides[get_invite_delivery] = lambda: DeliveryNotConfigured("email")

        assert _recover(client, ACTIVE.email or "").status_code == 202

    def test_nothing_is_minted_when_a_channel_is_missing(
        self, app: FastAPI, client: TestClient, gateway: _FakeGateway
    ) -> None:
        """Checked BEFORE the first side effect.

        An invitation nobody can receive must not leave a live challenge row
        behind, or a patient holds a code for a link that will never arrive.
        """
        app.dependency_overrides[get_sms_gateway] = lambda: DeliveryNotConfigured("SMS")

        _recover(client, ACTIVE.email or "")

        assert gateway.recorded == []
        assert gateway.commits == 0

    def test_a_gateway_that_raises_still_answers_202(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """A driver error must not become a second answer.

        Its message routinely quotes the offending value, which here is
        somebody's email address, so it goes to the log and the caller gets
        the 202 everybody else gets.
        """

        class _Broken:
            def resolve(self, slug: str) -> str | None:
                return TENANT

            def open(self, schema: str, request: Any) -> Any:
                raise RuntimeError(f"connection failed for {ACTIVE.email}")

        app.dependency_overrides[get_recovery_gateway] = _Broken

        assert _recover(client, ACTIVE.email or "").status_code == 202


class TestNoKnowledgeQuestionIsAsked:
    def test_the_request_body_accepts_only_an_address(self, client: TestClient) -> None:
        """No date of birth, no last visit, no last four digits.

        A knowledge check gives the caller a second answer to read and gates
        the recovery on something an acquaintance usually knows. Extra
        fields are simply ignored, so a client that sent one gets no
        different treatment.
        """
        response = client.post(
            f"/api/portal/practices/{SLUG}/recover",
            json={"email": ACTIVE.email, "date_of_birth": "1990-01-01"},
        )

        assert response.status_code == 202

    def test_a_wrong_extra_field_does_not_change_the_outcome(
        self, client: TestClient, delivery: CapturingInviteDelivery
    ) -> None:
        client.post(
            f"/api/portal/practices/{SLUG}/recover",
            json={"email": ACTIVE.email, "date_of_birth": "1800-01-01"},
        )

        assert len(delivery.sent) == 1


def test_the_minted_invitation_redeems_like_any_other(
    client: TestClient, challenges: InMemoryPortalAuthStore
) -> None:
    """Recovery reuses the clinician's mint path, so it inherits its bounds.

    The challenge it leaves behind is single-use, attempt-capped and
    expiring, exactly like the one an invite route writes — which is what
    "the same path" has to mean for the threat notes to hold.
    """
    _recover(client, ACTIVE.email or "")

    minted = [
        challenge
        for challenge in challenges._by_jti.values()
        if challenge.patient_id == ACTIVE.patient_id and challenge.jti != "expired-but-unspent"
    ]
    assert len(minted) == 1
    challenge = minted[0]
    assert challenge.consumed is False
    assert challenge.attempts == 0
    assert challenge.expires_at > int(time.time())
    assert challenge.otp_hash
