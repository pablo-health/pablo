# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Auth abstraction shared by every Epic token-acquisition strategy.

Both the interactive patient launch and the headless backend-services
flow produce the same :class:`AccessGrant`, so everything downstream of
the token (the FHIR client, the exporter, the eventual Pablo ingestion)
is agnostic to how the token was obtained.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AccessGrant:
    """An access token plus the context needed to use it."""

    access_token: str
    patient_id: str | None
    scope: str
    expires_in: int
    refresh_token: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class TokenProvider(Protocol):
    """Anything that can produce an :class:`AccessGrant` for FHIR calls."""

    def acquire(self) -> AccessGrant: ...
