# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give the end-to-end stack practices beyond the shared one.

Every account in the e2e stack used to land in the same practice, because
every account gets there the same way: signing up with no invite takes the
auto-provision path in ``app.auth.service``, which maps the identity onto
``DEFAULT_PRACTICE_ID`` by name. One practice means cross-tenant isolation is
never exercised THROUGH THE APP -- it is proven at the integration layer
against a NOBYPASSRLS role, which is the real boundary, but no browser spec
had ever asked whether one practice can read another's patients. That is the
claim the product rests on, and the layer a real user actually goes through
was the one layer not covered.

Two more practices exist for a different reason: some behaviour only happens
to a practice that has never answered a question, and the shared practice
answered it long ago. The first-client portal prompt is asked once, of a
practice that has never said whether it offers the portal; every worker
shares the default practice and the fixtures turn its portal on, so it can
never be asked there. ``e2e-fresh-yes`` and ``e2e-fresh-no`` are kept
unanswered — their portal answer is cleared on every run of this script — so
the spec for each path starts where a brand-new practice does.

THE LEVER is that the auto-provision mapping is idempotent and never
overwrites: an email that already resolves keeps whatever practice it resolves
to. So a mapping written HERE, before that address first signs in, wins, and
auto-provision returns early when the user arrives.

A script rather than a route or a startup hook, deliberately. Nothing in the
running product can reach this -- it is invoked once by the e2e compose stack
and by nothing else, so there is no test-only branch sitting in the request
path waiting to be reached in production by accident.

Idempotent: the stack can be restarted without dropping its volume, so this
must be safe to run against a database that already has these practices.

Usage (from the e2e compose stack, after migrations have run)::

    python -m backend.scripts.e2e_seed_second_practice
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Same shape as the other scripts here (clearinghouse_smoke, chat_gateway_smoke):
# the container's WORKDIR is /app with the source under /app/backend, while a
# developer runs this from the repo root, so the package root is put on the path
# explicitly rather than assumed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("e2e-seed-second-practice")


@dataclass(frozen=True)
class SeededPractice:
    """A practice this script provisions, and the one address that signs into it.

    Fixed rather than random, because two processes need to agree on them
    without talking: this script writes the mapping, and the e2e fixtures
    (``frontend/e2e/fixtures/auth.ts``, ``fixtures/freshPractice.ts``) sign a
    user in with it. ``make e2e-down`` drops the volume, so a fixed address
    cannot collide across runs.
    """

    id: str
    schema: str
    email: str
    name: str
    #: Clear the practice's portal answer on every run, so it is always a
    #: practice that has never been asked.
    unanswered: bool = False
    #: Tables in the practice's schema emptied on every run, so a spec that
    #: builds up state across its cases starts from nothing each bring-up.
    emptied: tuple[str, ...] = ()


SECOND_PRACTICE = SeededPractice(
    id="e2e-second",
    schema="practice_e2e_second",
    email="e2e-second-practice@example.com",
    name="Second Practice",
)
FRESH_YES = SeededPractice(
    id="e2e-fresh-yes",
    schema="practice_e2e_fresh_yes",
    email="e2e-fresh-yes@example.com",
    name="Fresh Practice Yes",
    unanswered=True,
)
FRESH_NO = SeededPractice(
    id="e2e-fresh-no",
    schema="practice_e2e_fresh_no",
    email="e2e-fresh-no@example.com",
    name="Fresh Practice No",
    unanswered=True,
)
# Its own practice for a spec that turns Messages off: doing that in the shared
# practice would pull the Messages page out from under the specs that use it.
FRESH_MESSAGES = SeededPractice(
    id="e2e-fresh-messages",
    schema="practice_e2e_fresh_messages",
    email="e2e-fresh-messages@example.com",
    name="Fresh Practice Messages",
    unanswered=True,
)
# Its own practice for the spec that follows a calendar feed: a feed puts a
# season of sessions on the calendar and a question on every one of them,
# which no spec sharing the default practice's calendar should have to see.
# Its cases build on each other's reads, so what they leave — the feed, its
# questions, the sessions booked and the charts — is emptied on every
# bring-up, the way the unanswered practices' portal answer is.
FRESH_FEED = SeededPractice(
    id="e2e-fresh-feed",
    schema="practice_e2e_fresh_feed",
    email="e2e-fresh-feed@example.com",
    name="Fresh Practice Feed",
    emptied=(
        "ical_sync_configs",
        "external_calendar_events",
        "appointments",
        "patient_source_mappings",
        "patients",
    ),
)
# Its own practice for the specs about a practice's own domains: once one of
# its portal hosts is the working primary, every portal link the practice
# sends goes to that host, which the specs sharing the default practice's
# invitations should never see. Its hosts are written by
# e2e_seed_practice_domains.py.
FRESH_DOMAINS = SeededPractice(
    id="e2e-fresh-domains",
    schema="practice_e2e_fresh_domains",
    email="e2e-fresh-domains@example.com",
    name="Fresh Practice Domains",
)
SEEDED = (SECOND_PRACTICE, FRESH_YES, FRESH_NO, FRESH_MESSAGES, FRESH_FEED, FRESH_DOMAINS)

# Kept for anything that still reads the second practice by its old names.
SECOND_PRACTICE_ID = SECOND_PRACTICE.id
SECOND_PRACTICE_SCHEMA = SECOND_PRACTICE.schema
SECOND_PRACTICE_EMAIL = SECOND_PRACTICE.email


def _seed(practice: SeededPractice) -> bool:
    """Register *practice*, map its address onto it, allow the address.

    Returns False when the address already resolves somewhere else — the
    specs that use it would then run in the wrong practice and prove nothing.
    """
    from app.db import create_standalone_session
    from app.db.platform_models import (
        EmailTenantMappingRow,
        PlatformAllowedEmailRow,
        PracticePortalSettingsRow,
        PracticeRow,
    )
    from app.utcnow import utc_now

    now = utc_now()
    session = create_standalone_session()
    try:
        if session.get(PracticeRow, practice.id) is None:
            session.add(
                PracticeRow(
                    id=practice.id,
                    name=practice.name,
                    schema_name=practice.schema,
                    tenant_id=practice.id,
                    owner_email=practice.email,
                    owner_user_id=None,
                    product="pablo",
                    status="active",
                    is_active=True,
                    created_at=now,
                )
            )
            logger.info("registered practice %s", practice.id)

        existing = session.get(EmailTenantMappingRow, practice.email)
        if existing is None:
            session.add(
                EmailTenantMappingRow(
                    email=practice.email,
                    tenant_id=practice.id,
                    practice_id=practice.id,
                    created_at=now,
                )
            )
            logger.info("mapped %s onto %s", practice.email, practice.id)
        elif existing.practice_id != practice.id:
            # Loud, because the alternative is a suite that silently proves
            # nothing: the isolation spec would sign both users into the same
            # practice and its "cannot reach" assertions would pass vacuously.
            logger.error(
                "%s already resolves to %s, not %s -- the specs using it would "
                "run in the wrong practice and pass without proving anything",
                practice.email,
                existing.practice_id,
                practice.id,
            )
            return False

        if session.get(PlatformAllowedEmailRow, practice.email) is None:
            # Without this the address passes the sign-up gate and is then
            # refused on every API call, which is a confusing way to discover
            # the same fact.
            session.add(
                PlatformAllowedEmailRow(
                    email=practice.email,
                    practice_id=practice.id,
                    added_by="e2e-seed",
                    added_at=now,
                )
            )

        if practice.unanswered:
            answered = session.get(PracticePortalSettingsRow, practice.id)
            if answered is not None:
                session.delete(answered)
                logger.info("cleared the portal answer of %s", practice.id)

        session.commit()
    finally:
        session.close()
    return True


def main() -> int:
    from app.db import get_engine
    from app.db.provisioning import create_practice_schema

    for practice in SEEDED:
        if not _seed(practice):
            return 1
        # Commits on its own connection, so the rows above are committed
        # first: a consistency check that looks sees a matching pair rather
        # than a schema with no practice behind it. Reconciling, so safe to
        # call on every boot.
        create_practice_schema(get_engine(), practice.schema)
        if practice.emptied:
            _empty(practice)
        logger.info("practice %s ready: schema %s", practice.id, practice.schema)
    return 0


def _empty(practice: SeededPractice) -> None:
    """Empty the tables a spec leaves behind, and whatever hangs off them.

    After the schema exists, so the first bring-up finds the tables too. The
    cascade takes the rows of tables that reference these (a session, a
    note) along with them; a test practice has nothing worth keeping.
    """
    from app.db import create_standalone_session
    from sqlalchemy import text

    tables = ", ".join(f"{practice.schema}.{table}" for table in practice.emptied)
    session = create_standalone_session()
    try:
        session.execute(text(f"TRUNCATE TABLE {tables} CASCADE"))
        session.commit()
    finally:
        session.close()
    logger.info("emptied %s in %s", ", ".join(practice.emptied), practice.id)


if __name__ == "__main__":
    sys.exit(main())
