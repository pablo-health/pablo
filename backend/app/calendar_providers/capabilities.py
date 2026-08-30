# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The vocabulary the calendar seam speaks: capabilities, not scopes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class CalendarCapability(Enum):
    """What Pablo wants a therapist's calendar for.

    Declaration order is the order scopes are requested in, so it stays
    stable no matter what order a caller lists capabilities.
    """

    PUSH = "push"
    """Write Pablo's sessions out to the calendar."""

    BUSY = "busy"
    """Know when the therapist is otherwise booked. Times, never titles."""

    IMPORT = "import"
    """Read event content once, to propose an existing practice."""


class NarrowingEnforcement(Enum):
    """Who guarantees a capability cannot reach past what it is for.

    The distinction is user-visible, not bookkeeping: consent copy is
    rendered from it, so a provider whose grant reaches further than the
    feature does can never render a promise claiming otherwise.
    """

    PROVIDER_ENFORCED = "provider_enforced"
    """The grant itself cannot reach further."""

    PABLO_ENFORCED = "pablo_enforced"
    """The grant could reach further; Pablo's own code is what doesn't."""


class UnsupportedCapabilityError(ValueError):
    """A capability was asked of a provider that does not declare it."""


@dataclass(frozen=True)
class ProviderCapability:
    """One provider's declaration of how it satisfies one capability."""

    capability: CalendarCapability
    scopes: tuple[str, ...]
    """The narrowest scope set on this provider that satisfies the capability."""

    incremental: bool
    """Whether the grant is requested when the feature is first used, rather
    than at connect. A therapist who never imports never grants content read."""

    enforcement: NarrowingEnforcement
    reach: str
    """What the capability is limited to, in a therapist's words. Consent copy
    reads this, so it is a phrase that completes "... only for {reach}"."""

    def __post_init__(self) -> None:
        if not self.scopes:
            raise ValueError(f"{self.capability.value} declares no scopes")
        if not self.reach:
            raise ValueError(f"{self.capability.value} declares no reach")


def scopes_for(
    declarations: Mapping[CalendarCapability, ProviderCapability],
    capabilities: Iterable[CalendarCapability],
) -> tuple[str, ...]:
    """Scopes satisfying every requested capability, de-duplicated.

    A lookup over declarations, not a chain of provider branches: adding a
    provider is a new mapping, never a new branch here.
    """
    requested = frozenset(capabilities)
    missing = requested - frozenset(declarations)
    if missing:
        names = ", ".join(sorted(capability.value for capability in missing))
        raise UnsupportedCapabilityError(f"provider does not declare: {names}")

    scopes: list[str] = []
    for capability in CalendarCapability:
        if capability not in requested:
            continue
        for scope in declarations[capability].scopes:
            if scope not in scopes:
                scopes.append(scope)
    return tuple(scopes)
