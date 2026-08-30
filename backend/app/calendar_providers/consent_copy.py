# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Consent copy, rendered from a provider's own capability declarations.

What a connect screen may promise depends on who enforces the limit. Where
the grant itself cannot reach past the feature, we can say so plainly.
Where it can and only Pablo's code holds the line, saying so would be
telling a therapist their calendar is unreachable when it is reachable.

So the copy is generated from the declaration rather than written next to
it. There is no template a caller can reach that asserts a guarantee the
declaration doesn't carry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import NarrowingEnforcement, scopes_for

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from .capabilities import CalendarCapability, ProviderCapability

_PROMISE_TEMPLATES: Mapping[NarrowingEnforcement, str] = {
    NarrowingEnforcement.PROVIDER_ENFORCED: (
        "{provider} limits this to {reach}. Pablo cannot reach further, "
        "because the permission itself does not."
    ),
    NarrowingEnforcement.PABLO_ENFORCED: (
        "Pablo uses this only for {reach}. The permission {provider} grants "
        "covers more than that, so the limit is Pablo's own."
    ),
}


def capability_promise(display_name: str, declaration: ProviderCapability) -> str:
    """One line describing what a capability reaches, and who keeps it there."""
    template = _PROMISE_TEMPLATES.get(declaration.enforcement)
    if template is None:
        raise ValueError(f"no consent copy for enforcement {declaration.enforcement}")
    return template.format(provider=display_name, reach=declaration.reach)


def consent_promises(
    display_name: str,
    declarations: Mapping[CalendarCapability, ProviderCapability],
    capabilities: Collection[CalendarCapability],
) -> tuple[str, ...]:
    """The promises to show for a set of capabilities, in declaration order."""
    # scopes_for rejects anything the provider doesn't declare, so a caller
    # can't get copy for a capability it will never be granted.
    scopes_for(declarations, capabilities)
    requested = frozenset(capabilities)
    return tuple(
        capability_promise(display_name, declaration)
        for capability, declaration in declarations.items()
        if capability in requested
    )
