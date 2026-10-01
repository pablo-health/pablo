# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Give the end-to-end stack's shared practice two portal hosts that already work.

A host becomes ``active`` only when whatever serves it has confirmed it, and
the engine has no route that says so on a practice's behalf — deliberately,
since a practice could otherwise declare a host working that nobody checked.
So a browser spec can add and remove hosts, but cannot make one active, and
choosing a primary is only possible among active hosts.

This writes two active portal hosts for the shared practice so
``practice-domains.spec.ts`` can choose between them. A script rather than a
route for the same reason as ``e2e_seed_second_practice.py``: it is invoked
once by the e2e compose stack and by nothing else, so there is no test-only
branch in the request path.

Idempotent: a host already present is left as it is, primary or not.

Usage (from the e2e compose stack, after the shared practice exists)::

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

ACTIVE_PORTAL_HOSTS = ("portal.e2e-practice.example", "clients.e2e-practice.example")


def main() -> int:
    from app.db import DEFAULT_PRACTICE_ID, create_standalone_session
    from app.db.platform_models import PracticeDomainRow
    from app.utcnow import utc_now

    now = utc_now()
    session = create_standalone_session()
    try:
        for host in ACTIVE_PORTAL_HOSTS:
            existing = session.get(PracticeDomainRow, host)
            if existing is not None:
                continue
            session.add(
                PracticeDomainRow(
                    domain=host,
                    practice_id=DEFAULT_PRACTICE_ID,
                    purpose="portal",
                    kind="vanity",
                    status="active",
                    is_primary=False,
                    verified_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            logger.info("seeded active portal host %s", host)
        session.commit()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
