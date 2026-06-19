"""Provider prefixes for ``provider:model`` model strings.

Model ids may carry a LiteLLM-style provider prefix (e.g.
``google:gemini-3.1-pro``). Vertex AI wants the bare id — it concatenates the
string into ``publishers/google/models/<model>`` and returns
``400 INVALID_ARGUMENT`` for the colon. ``strip_provider_prefix`` removes a
known prefix; an unknown prefix or a bare model id is returned unchanged.

This is the one place the set of known prefixes lives, so the gateways don't
each carry their own literal.
"""

from __future__ import annotations

from enum import StrEnum


class LLMProvider(StrEnum):
    """Known provider prefixes used in ``provider:model`` model strings."""

    GOOGLE = "google"


_KNOWN_PREFIXES: frozenset[str] = frozenset(p.value for p in LLMProvider)


def strip_provider_prefix(model: str) -> str:
    """Return *model* with a known ``provider:`` prefix removed.

    ``google:gemini-3.1-pro`` -> ``gemini-3.1-pro``. A bare model id, or one
    whose prefix is not a recognized provider, is returned unchanged.
    """
    provider, sep, rest = model.partition(":")
    if sep and provider in _KNOWN_PREFIXES:
        return rest
    return model
