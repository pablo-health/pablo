# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable chat-model resolution hook (THERAPY-5x5 acceptance criteria).

The default is model-neutral: every caller resolves to
``settings.ai_model_flash`` (with ``settings.ai_model`` as a fallback)
unless the caller passed an explicit per-conversation override.

The hook is dependency-injectable so a downstream consumer can
substitute a tier-aware resolver (per-tier model selection, per-user
or per-``caller_feature_key`` overrides) without touching this module.
By design, no clinical or tier opinion ships in this distribution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ..settings import get_settings

if TYPE_CHECKING:
    from ..models import User

# A resolver takes a user, the caller's feature key, and an optional
# per-conversation override. It returns the model id to pass to the
# Gemini gateway.
ChatModelResolver = Callable[..., str]


def default_resolve_chat_model(
    *,
    user: User | None,  # noqa: ARG001 — kept for override-hook parity
    feature_key: str,  # noqa: ARG001 — same
    override: str | None = None,
) -> str:
    """Default resolver. Honors a per-conversation override; else
    falls back to ``settings.ai_model_flash`` (or ``settings.ai_model``).

    The unused parameters are part of the public hook signature so a
    downstream consumer can ship a drop-in replacement that *does* use
    them without breaking callers. Suppressing ARG001 on this side
    keeps the default implementation noise-free.
    """
    if override:
        return override
    settings = get_settings()
    return settings.ai_model_flash or settings.ai_model


_resolver: ChatModelResolver = default_resolve_chat_model


def get_chat_model_resolver() -> ChatModelResolver:
    """FastAPI dependency hook. Downstream consumers substitute via
    ``app.dependency_overrides[get_chat_model_resolver] = ...``."""
    return _resolver


__all__ = [
    "ChatModelResolver",
    "default_resolve_chat_model",
    "get_chat_model_resolver",
]
