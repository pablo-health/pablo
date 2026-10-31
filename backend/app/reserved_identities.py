# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reserved test-identity email shapes shared by the app and the pentest tooling.

Both prefixes live under the real ``pablo.health`` domain with an 8-hex
suffix, so a collision with any real account is statistically impossible
and visually obvious to auditors. Real signups matching either pattern
are rejected server-side; the bypass that honors them (``auth.service``)
is gated on ``settings.is_prod_project``.

This module is deliberately stdlib-only. The pentest runner imports it
from a container that has none of the app's dependencies installed, and
the app imports it instead of reaching into ``app.jobs.pentest_*`` — the
product image must never need the pentest tooling (PABLO-8gna.24).
"""

import re

PENTEST_EMAIL_PREFIX = "pentestuser-"
PENTEST_EMAIL_DOMAIN = "pablo.health"
PENTEST_EMAIL_PATTERN = re.compile(
    rf"^{re.escape(PENTEST_EMAIL_PREFIX)}[0-9a-f]{{8}}@{re.escape(PENTEST_EMAIL_DOMAIN)}$"
)

E2E_EMAIL_PATTERN = re.compile(r"^e2etest-[0-9a-f]{8}@pablo\.health$")
