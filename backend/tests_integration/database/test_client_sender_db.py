# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Who a practice's client email is from, against real PostgreSQL.

The rule in ``app.portal.client_sender`` reads four platform tables — the
practice, its sender settings, its domains and their email identities, and the
addresses its people sign in with — so it is proven here on committed rows,
through the code's own sessions:

* no row: the practice's name, ``portal``, and the owner's address;
* a verified domain: ``<mailbox>@<domain>``; a pending or failed one: the
  deployment's address, under the practice's name;
* several verified domains: the one the primary portal host sits under, else
  the oldest;
* never a person's own mailbox, refused on save and, if a domain added later
  makes it one, not sent from;
* the settings store's round trip.

The validation and the routes are unit-tested in ``tests/test_client_sender.py``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.portal.client_sender import (
    PlatformSenderSettingsStore,
    SenderSettings,
    SenderSettingsError,
    resolve_client_sender,
    resolve_client_sender_for_schema,
)
from app.portal.delivery import ClientSender
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_PRACTICE_NAME = "Example Therapy"
_OWNER = "owner@example.com"
_T0 = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass
class _Rows:
    """Every platform row a test wrote, so it can all be taken out again."""

    engine: Engine
    practices: list[str] = field(default_factory=list)
    apexes: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)

    def practice(self, *, owner_email: str = _OWNER) -> str:
        practice_id = f"sender-{uuid.uuid4().hex[:10]}"
        self.practices.append(practice_id)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practices"
                    " (id, name, schema_name, owner_email, product, status, is_active,"
                    "  created_at)"
                    " VALUES (:id, :name, :schema, :owner, 'pablo', 'active', TRUE, now())"
                ),
                {
                    "id": practice_id,
                    "name": _PRACTICE_NAME,
                    "schema": f"practice_{practice_id.replace('-', '_')}",
                    "owner": owner_email,
                },
            )
        return practice_id

    def apex(self, practice_id: str, *, status: str | None = "verified", age_days: int = 0) -> str:
        """A domain the practice holds, created *age_days* after a fixed day."""
        apex = f"{uuid.uuid4().hex[:10]}.example.com"
        self.apexes.append(apex)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domain_apexes"
                    " (apex, practice_id, verify_token, email_identity_status, created_at)"
                    " VALUES (:a, :p, 'token', :s, :t)"
                ),
                {"a": apex, "p": practice_id, "s": status, "t": _T0 + timedelta(days=age_days)},
            )
        return apex

    def primary_portal_host(self, practice_id: str, apex: str, *, status: str = "active") -> None:
        host = f"portal.{apex}"
        self.hosts.append(host)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.practice_domains"
                    " (domain, practice_id, purpose, kind, status, is_primary, created_at)"
                    " VALUES (:d, :p, 'portal', 'vanity', :s, TRUE, now())"
                ),
                {"d": host, "p": practice_id, "s": status},
            )

    def sign_in_address(self, practice_id: str, email: str) -> None:
        self.emails.append(email)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO platform.email_tenant_mappings"
                    " (email, tenant_id, practice_id, created_at) VALUES (:e, 't', :p, now())"
                ),
                {"e": email, "p": practice_id},
            )

    def remove_all(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM platform.practice_domains WHERE domain = ANY(:h)"),
                {"h": self.hosts},
            )
            conn.execute(
                text("DELETE FROM platform.practice_domain_apexes WHERE apex = ANY(:a)"),
                {"a": self.apexes},
            )
            conn.execute(
                text("DELETE FROM platform.email_tenant_mappings WHERE email = ANY(:e)"),
                {"e": self.emails},
            )
            for table in ("practice_email_senders", "practices"):
                column = "id" if table == "practices" else "practice_id"
                conn.execute(
                    text(f"DELETE FROM platform.{table} WHERE {column} = ANY(:p)"),  # noqa: S608 — fixed names
                    {"p": self.practices},
                )


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def rows(engine: Engine) -> Iterator[_Rows]:
    written = _Rows(engine)
    yield written
    written.remove_all()


@pytest.fixture
def store() -> PlatformSenderSettingsStore:
    return PlatformSenderSettingsStore()


# ── the rule ──────────────────────────────────────────────────────────────


def test_with_nothing_chosen_and_no_domain_mail_is_the_deployments_under_the_practice_name(
    rows: _Rows,
) -> None:
    practice_id = rows.practice()

    assert resolve_client_sender(practice_id) == ClientSender(
        from_name=_PRACTICE_NAME, from_address=None, reply_to=_OWNER
    )


def test_a_verified_domain_sends_as_portal_at_that_domain(rows: _Rows) -> None:
    practice_id = rows.practice()
    apex = rows.apex(practice_id)

    assert resolve_client_sender(practice_id) == ClientSender(
        from_name=_PRACTICE_NAME, from_address=f"portal@{apex}", reply_to=_OWNER
    )


@pytest.mark.parametrize("status", ["pending", "failed", None])
def test_a_domain_whose_email_is_not_verified_does_not_send(
    rows: _Rows, status: str | None
) -> None:
    practice_id = rows.practice()
    rows.apex(practice_id, status=status)

    sender = resolve_client_sender(practice_id)

    assert sender.from_address is None
    assert sender.from_name == _PRACTICE_NAME


def test_the_practices_choices_are_what_is_sent(
    rows: _Rows, store: PlatformSenderSettingsStore
) -> None:
    practice_id = rows.practice()
    apex = rows.apex(practice_id)
    store.save(
        practice_id,
        SenderSettings(
            sender_name="Jordan Rivera, LCSW",
            sender_local_part="hello",
            reply_to="frontdesk@example.org",
        ),
        by="user-1",
    )

    assert resolve_client_sender(practice_id) == ClientSender(
        from_name="Jordan Rivera, LCSW",
        from_address=f"hello@{apex}",
        reply_to="frontdesk@example.org",
    )


def test_with_two_verified_domains_the_primary_portal_hosts_wins(rows: _Rows) -> None:
    practice_id = rows.practice()
    rows.apex(practice_id, age_days=0)
    newer = rows.apex(practice_id, age_days=5)
    rows.primary_portal_host(practice_id, newer)

    assert resolve_client_sender(practice_id).from_address == f"portal@{newer}"


def test_a_primary_host_on_an_unverified_domain_leaves_the_oldest_verified_one(
    rows: _Rows,
) -> None:
    practice_id = rows.practice()
    oldest = rows.apex(practice_id, age_days=0)
    rows.apex(practice_id, age_days=3)
    pending = rows.apex(practice_id, status="pending", age_days=1)
    rows.primary_portal_host(practice_id, pending)

    assert resolve_client_sender(practice_id).from_address == f"portal@{oldest}"


def test_with_no_primary_host_the_oldest_verified_domain_wins(rows: _Rows) -> None:
    practice_id = rows.practice()
    rows.apex(practice_id, age_days=9)
    oldest = rows.apex(practice_id, age_days=2)

    assert resolve_client_sender(practice_id).from_address == f"portal@{oldest}"


def test_a_mailbox_that_became_someones_own_address_is_not_sent_from(rows: _Rows) -> None:
    """Saved before the domain existed; the send falls back rather than going out as a person."""
    practice_id = rows.practice()
    apex = rows.apex(practice_id)
    rows.sign_in_address(practice_id, f"portal@{apex}")

    sender = resolve_client_sender(practice_id)

    assert sender.from_address is None
    assert sender.from_name == _PRACTICE_NAME


def test_resolving_by_schema_finds_the_practice_living_there(rows: _Rows) -> None:
    practice_id = rows.practice()
    schema = f"practice_{practice_id.replace('-', '_')}"

    resolved = resolve_client_sender_for_schema(schema)

    assert resolved is not None
    assert resolved.from_name == _PRACTICE_NAME
    assert resolve_client_sender_for_schema("practice_nobody_lives_here") is None


# ── the store ─────────────────────────────────────────────────────────────


def test_the_store_round_trips_and_clearing_goes_back_to_the_defaults(
    rows: _Rows, store: PlatformSenderSettingsStore
) -> None:
    practice_id = rows.practice()
    chosen = SenderSettings(
        sender_name="Jordan Rivera, LCSW", sender_local_part="hello", reply_to="a@example.org"
    )

    view = store.save(practice_id, chosen, by="user-1")
    assert view.chosen == chosen
    assert view.defaults.sender_name == _PRACTICE_NAME
    assert view.defaults.sender_local_part == "portal"
    assert view.defaults.reply_to == _OWNER
    assert view.sending_domain is None

    cleared = store.save(practice_id, SenderSettings(), by="user-1")
    assert cleared.chosen == SenderSettings()
    assert cleared.effective == ClientSender(
        from_name=_PRACTICE_NAME, from_address=None, reply_to=_OWNER
    )


def test_the_view_names_the_domain_mail_leaves_from(
    rows: _Rows, store: PlatformSenderSettingsStore
) -> None:
    practice_id = rows.practice()
    apex = rows.apex(practice_id)

    view = store.view(practice_id)

    assert view.sending_domain == apex
    assert view.effective.from_address == f"portal@{apex}"


def test_a_mailbox_that_is_the_reply_to_address_on_a_held_domain_is_refused(
    rows: _Rows, store: PlatformSenderSettingsStore
) -> None:
    """Checked against every domain the practice holds, not only verified ones."""
    practice_id = rows.practice()
    apex = rows.apex(practice_id, status="pending")

    with pytest.raises(SenderSettingsError, match="someone's own address"):
        store.save(
            practice_id,
            SenderSettings(sender_local_part="frontdesk", reply_to=f"frontdesk@{apex}"),
            by="user-1",
        )
    assert store.view(practice_id).chosen == SenderSettings()


def test_a_mailbox_that_is_the_owners_address_is_refused(
    rows: _Rows, store: PlatformSenderSettingsStore
) -> None:
    """The default reply-to is the owner's address, so the owner's mailbox is someone's too."""
    apex_owner = f"owner@{uuid.uuid4().hex[:10]}.example.com"
    practice_id = rows.practice(owner_email=apex_owner)
    domain = apex_owner.split("@", 1)[1]
    rows.apexes.append(domain)
    with rows.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practice_domain_apexes"
                " (apex, practice_id, verify_token, email_identity_status, created_at)"
                " VALUES (:a, :p, 'token', 'verified', now())"
            ),
            {"a": domain, "p": practice_id},
        )

    with pytest.raises(SenderSettingsError):
        store.save(practice_id, SenderSettings(sender_local_part="owner"), by="user-1")


def test_a_practice_that_does_not_exist_is_a_lookup_error() -> None:
    with pytest.raises(LookupError):
        resolve_client_sender("no-such-practice")
