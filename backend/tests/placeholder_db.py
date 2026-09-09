# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The unit suite's placeholder ``DATABASE_URL``, named in one place.

The unit suite never connects to a database, but ``app.settings`` validates
``DATABASE_URL`` at import, so ``tests/conftest.py`` plants a value to get past
validation. The integration suite reads the same variable to mean something
entirely different: "the caller supplied a real database, don't start a
container".

One string, two incompatible meanings, and no way for the second reader to tell
which it is holding. Run both suites in one pytest invocation and the unit
conftest's placeholder is already in the environment by the time the integration
conftest looks, so it stands down, never sets ``DATABASE_BACKEND=postgres``, and
every integration module's ``skipif`` fires. The run exits 0 having executed
none of them — a green signal for work that never ran (PABLO-1vep).

Comparing URL strings would be the obvious fix and the wrong one: a developer
whose real scratch database genuinely lives at that URL would be locked out for
choosing an unlucky name. So the placeholder announces itself with a marker
variable instead. Only the code that PLANTS the placeholder sets it, so its
presence means "this value is a stand-in", not "this value looks like one".
"""

from __future__ import annotations

import os

#: What the unit suite plants so settings validation passes. Never connected to.
PLACEHOLDER_DATABASE_URL = "postgresql://test:test@localhost:5432/test"

#: Set only by whoever actually plants the placeholder. A caller who exported a
#: real ``DATABASE_URL`` never sets it, which is what keeps the bring-your-own-
#: database workflow working.
PLACEHOLDER_MARKER_ENV = "PABLO_PLACEHOLDER_DATABASE_URL"


def plant_placeholder_database_url() -> bool:
    """Set the placeholder ``DATABASE_URL`` if the caller supplied none.

    Returns whether it planted one. The marker is set in the same breath, so
    the two can never drift apart — a placeholder without its marker is exactly
    the silent-skip bug this module exists to prevent.
    """
    if os.environ.get("DATABASE_URL"):
        return False
    os.environ["DATABASE_URL"] = PLACEHOLDER_DATABASE_URL
    os.environ[PLACEHOLDER_MARKER_ENV] = "1"
    return True


def database_url_is_placeholder() -> bool:
    """Whether the ``DATABASE_URL`` in the environment is the unit suite's stand-in."""
    return bool(os.environ.get(PLACEHOLDER_MARKER_ENV))
