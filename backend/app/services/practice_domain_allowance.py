# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""How many of its own domains a practice may use: the extension point.

What counts is registrable domains, not hostnames — ``portal.example.com``,
``example.com`` and ``www.example.com`` are one. Whether there is a limit, what
it is, and what a practice is told when it reaches it are configurable per
deployment, so the engine ships none: with no policy registered a practice may
add as many domains as it likes. A deployment that wants a limit registers a
policy at startup.

Adding a host under a domain the practice already uses is never refused by the
allowance; only a host that would bring in another domain is.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class DomainAllowance:
    #: How many registrable domains the practice may use.
    limit: int
    #: What the practice is told when adding one more is refused.
    message: str


#: Given a practice id, its allowance, or ``None`` for no limit.
type DomainAllowancePolicy = Callable[[str], DomainAllowance | None]

_policy: DomainAllowancePolicy | None = None


def register_domain_allowance(policy: DomainAllowancePolicy) -> None:
    """Supply the policy that limits a practice's domains. Called once at
    startup; the policy is asked on every add, so it may look the practice up."""
    global _policy  # noqa: PLW0603
    _policy = policy


def domain_allowance(practice_id: str) -> DomainAllowance | None:
    """The practice's allowance, or ``None`` when there is no limit."""
    return _policy(practice_id) if _policy is not None else None


def reset_domain_allowance() -> None:
    """Drop the registration. For tests, so one case's policy does not leak
    into the next through the module-level slot."""
    global _policy  # noqa: PLW0603
    _policy = None
