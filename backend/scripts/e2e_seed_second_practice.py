# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give the end-to-end stack a SECOND practice, so isolation can be tested.

Every account in the e2e stack used to land in the same practice, because
every account gets there the same way: signing up with no invite takes the
auto-provision path in ``app.auth.service``, which maps the identity onto
``DEFAULT_PRACTICE_ID`` by name. One practice means cross-tenant isolation is
never exercised THROUGH THE APP -- it is proven at the integration layer
against a NOBYPASSRLS role, which is the real boundary, but no browser spec
had ever asked whether one practice can read another's patients. That is the
claim the product rests on, and the layer a real user actually goes through
was the one layer not covered.

THE LEVER is that the auto-provision mapping is idempotent and never
overwrites: an email that already resolves keeps whatever practice it resolves
to. So a mapping written HERE, before that address first signs in, wins, and
auto-provision returns early when the user arrives.

A script rather than a route or a startup hook, deliberately. Nothing in the
running product can reach this -- it is invoked once by the e2e compose stack
and by nothing else, so there is no test-only branch sitting in the request
path waiting to be reached in production by accident.

Idempotent: the stack can be restarted without dropping its volume, so this
must be safe to run against a database that already has the second practice.

Usage (from the e2e compose stack, after migrations have run)::

    python -m backend.scripts.e2e_seed_second_practice
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Same shape as the other scripts here (clearinghouse_smoke, chat_gateway_smoke):
# the container's WORKDIR is /app with the source under /app/backend, while a
# developer runs this from the repo root, so the package root is put on the path
# explicitly rather than assumed.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("e2e-seed-second-practice")

# Fixed rather than random, because two processes need to agree on it without
# talking: this script writes the mapping, and frontend/e2e/fixtures/auth.ts
# signs a user in with it. The suite runs workers: 1 and `make e2e-down` drops
# the volume, so a fixed address cannot collide across runs.
SECOND_PRACTICE_ID = "e2e-second"
SECOND_PRACTICE_SCHEMA = "practice_e2e_second"
SECOND_PRACTICE_EMAIL = "e2e-second-practice@example.com"


def main() -> int:
    from app.db import create_standalone_session, get_engine
    from app.db.platform_models import (
        EmailTenantMappingRow,
        PlatformAllowedEmailRow,
        PracticeRow,
    )
    from app.db.provisioning import create_practice_schema
    from app.utcnow import utc_now

    now = utc_now()

    session = create_standalone_session()
    try:
        if session.get(PracticeRow, SECOND_PRACTICE_ID) is None:
            session.add(
                PracticeRow(
                    id=SECOND_PRACTICE_ID,
                    name="Second Practice",
                    schema_name=SECOND_PRACTICE_SCHEMA,
                    tenant_id=SECOND_PRACTICE_ID,
                    owner_email=SECOND_PRACTICE_EMAIL,
                    owner_user_id=None,
                    product="pablo",
                    status="active",
                    is_active=True,
                    created_at=now,
                )
            )
            logger.info("registered practice %s", SECOND_PRACTICE_ID)

        existing = session.get(EmailTenantMappingRow, SECOND_PRACTICE_EMAIL)
        if existing is None:
            session.add(
                EmailTenantMappingRow(
                    email=SECOND_PRACTICE_EMAIL,
                    tenant_id=SECOND_PRACTICE_ID,
                    practice_id=SECOND_PRACTICE_ID,
                    created_at=now,
                )
            )
            logger.info("mapped %s onto %s", SECOND_PRACTICE_EMAIL, SECOND_PRACTICE_ID)
        elif existing.practice_id != SECOND_PRACTICE_ID:
            # Loud, because the alternative is a suite that silently proves
            # nothing: the isolation spec would sign both users into the same
            # practice and its "cannot reach" assertions would pass vacuously.
            logger.error(
                "%s already resolves to %s, not %s -- the isolation specs would "
                "run both users in one practice and pass without proving anything",
                SECOND_PRACTICE_EMAIL,
                existing.practice_id,
                SECOND_PRACTICE_ID,
            )
            return 1

        if session.get(PlatformAllowedEmailRow, SECOND_PRACTICE_EMAIL) is None:
            # Without this the address passes the sign-up gate and is then
            # refused on every API call, which is a confusing way to discover
            # the same fact.
            session.add(
                PlatformAllowedEmailRow(
                    email=SECOND_PRACTICE_EMAIL,
                    practice_id=SECOND_PRACTICE_ID,
                    added_by="e2e-seed",
                    added_at=now,
                )
            )

        session.commit()
    finally:
        session.close()

    # Commits on its own connection, so the rows above are committed first: a
    # consistency check that looks sees a matching pair rather than a schema
    # with no practice behind it. Reconciling, so safe to call on every boot.
    create_practice_schema(get_engine(), SECOND_PRACTICE_SCHEMA)
    logger.info("second practice ready: schema %s", SECOND_PRACTICE_SCHEMA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
