# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which redirect URIs an OAuth round trip may come back to.

The redirect URI is where a vendor sends the authorization code, and the code
is spent by whoever holds it. So it is the one parameter of a connect flow
that a caller must not be able to choose freely: an unchecked value turns
every OAuth route into somewhere to have somebody else's code delivered.

One list for every vendor. The set has only ever had three kinds of entry —
this deployment's own frontends, localhost for development, and the desktop
app's custom schemes — and a second copy of it would be a second thing to
remember when one changes.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..settings import get_settings

NATIVE_APP_SCHEMES = frozenset({"pablohealth", "therapyrecorder"})
"""Custom schemes the desktop app registers, which have no origin to match."""


def is_allowed_oauth_redirect_uri(redirect_uri: str) -> bool:
    """Whether an authorization code may be delivered to this URI."""
    try:
        parsed = urlparse(redirect_uri)
    except Exception:
        return False

    if parsed.scheme in NATIVE_APP_SCHEMES:
        return True

    if parsed.scheme == "http" and parsed.hostname == "localhost":
        return True

    settings = get_settings()
    allowed_origins = {o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()}
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return origin in allowed_origins
