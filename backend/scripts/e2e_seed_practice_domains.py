# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give the end-to-end stack's domains practice two portal hosts that already work.

A host becomes ``active`` only when whatever serves it has confirmed it, and
the engine has no route that says so on a practice's behalf — deliberately,
since a practice could otherwise declare a host working that nobody checked.
So a browser spec can add and remove hosts, but cannot make one active, and
choosing a primary is only possible among active hosts.

This writes two active portal hosts, plus one still pending, for the practice
``e2e_seed_second_practice.py`` provisions for these specs
(``e2e-fresh-domains``), so ``practice-domains.spec.ts`` can choose between
them and ``practice-own-host.spec.ts`` can be served on them. Not the shared
practice: once one of these is the working primary, every portal link the
practice sends points at it, and the specs that open the shared practice's
invitations must not depend on whether those ran first.

A script rather than a route for the same reason as
``e2e_seed_second_practice.py``: it is invoked once by the e2e compose stack
and by nothing else, so there is no test-only branch in the request path.

Idempotent: a host already present is left as it is, primary or not.

Usage (from the e2e compose stack, after ``e2e_seed_second_practice.py``)::

    python backend/scripts/e2e_seed_practice_domains.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Same path setup as e2e_seed_second_practice.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("e2e-seed-practice-domains")

#: The practice ``e2e_seed_second_practice.FRESH_DOMAINS`` provisions.
DOMAINS_PRACTICE_ID = "e2e-fresh-domains"
ACTIVE_PORTAL_HOSTS = ("portal.e2e-practice.example", "clients.e2e-practice.example")
PENDING_PORTAL_HOSTS = ("pending.e2e-practice.example",)


def main() -> int:
    from app.db import create_standalone_session
    from app.db.platform_models import PracticeDomainRow
    from app.utcnow import utc_now

    now = utc_now()
    session = create_standalone_session()
    try:
        hosts = [(h, "active") for h in ACTIVE_PORTAL_HOSTS]
        hosts += [(h, "pending") for h in PENDING_PORTAL_HOSTS]
        for host, status in hosts:
            existing = session.get(PracticeDomainRow, host)
            if existing is not None:
                continue
            session.add(
                PracticeDomainRow(
                    domain=host,
                    practice_id=DOMAINS_PRACTICE_ID,
                    purpose="portal",
                    kind="vanity",
                    status=status,
                    is_primary=False,
                    verified_at=now if status == "active" else None,
                    created_at=now,
                    updated_at=now,
                )
            )
            logger.info("seeded %s portal host %s", status, host)
        session.commit()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
