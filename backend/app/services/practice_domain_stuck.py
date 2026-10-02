# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a practice is told when one of its hosts is taking too long: the
extension point.

A host is taking too long when every record it needs has been in place for
longer than ``practice_domain_stuck_after_seconds`` and it is still not active.
Nothing stops checking it — the usual causes (DNS caches, a certificate issuer
retrying) clear up by themselves, and the host then goes active as usual — but
the practice has done its part, so it is told who can help. Who that is depends
on the deployment, so the engine's own words name nobody; a deployment that
wants to say more registers its message at startup.
"""

from __future__ import annotations

#: Shown when no deployment registered a message.
DEFAULT_STUCK_MESSAGE = "This is taking longer than usual. Contact your administrator for help."

_message: str | None = None


def register_domain_stuck_message(message: str) -> None:
    """Supply what a practice is told about a host that is taking too long.
    Called once at startup."""
    global _message  # noqa: PLW0603
    _message = message


def domain_stuck_message() -> str:
    """The registered message, or the engine's own."""
    return _message if _message is not None else DEFAULT_STUCK_MESSAGE


def reset_domain_stuck_message() -> None:
    """Drop the registration. For tests, so one case's message does not leak
    into the next through the module-level slot."""
    global _message  # noqa: PLW0603
    _message = None
