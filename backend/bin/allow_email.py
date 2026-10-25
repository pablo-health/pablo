# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Admit an email to the deployment's own practice: ``allow_email.py <email>``.

The installer's way in for the first clinician. Signups are restricted, so a
fresh install admits nobody; this writes the allowlist grant and the
email-to-practice mapping together (``AllowlistRepository.add``), and the
account is provisioned on that person's first sign-in. Idempotent.

Run after ``migrate.py``, which builds the platform schema this writes to.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Admit an email to the deployment's practice.")
    parser.add_argument("email")
    email: str = parser.parse_args().email.lower()
    if "@" not in email:
        parser.error(f"not an email address: {email}")

    os.chdir(Path(__file__).resolve().parent.parent)
    sys.path.insert(0, str(Path.cwd()))

    from app.db import DEFAULT_PRACTICE_ID, create_standalone_session
    from app.repositories.postgres.allowlist import PostgresAllowlistRepository

    with create_standalone_session() as session:
        PostgresAllowlistRepository(session).add(
            email, "install.sh", practice_id=DEFAULT_PRACTICE_ID
        )
        session.commit()
    print(f"Allowed {email} into practice '{DEFAULT_PRACTICE_ID}'")
