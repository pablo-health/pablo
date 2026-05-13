# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable chat-model resolution hook (THERAPY-5x5 acceptance criteria).

The OSS default is model-neutral: every caller resolves to
``settings.ai_model_flash`` (with ``settings.ai_model`` as a fallback)
unless the caller passed an explicit per-conversation override.

The hook is dependency-injectable so the SaaS overlay can substitute
a tier-aware resolver (Solo → Flash-Lite, Practice+ → Pro, with
per-user / per-``caller_feature_key`` overrides) without touching OSS.
Per the bead acceptance criteria, no clinical or tier opinion ships
under AGPL — that lives in SaaS.
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
    user: User | None,  # noqa: ARG001 — kept for SaaS overlay parity
    feature_key: str,  # noqa: ARG001 — same
    override: str | None = None,
) -> str:
    """OSS resolver. Honors a per-conversation override; else falls
    back to ``settings.ai_model_flash`` (or ``settings.ai_model``).

    The unused parameters are part of the public hook signature so a
    SaaS overlay can ship a drop-in replacement that *does* use them
    without breaking callers. Suppressing ARG001 on this side keeps the
    OSS implementation noise-free.
    """
    if override:
        return override
    settings = get_settings()
    return settings.ai_model_flash or settings.ai_model


_resolver: ChatModelResolver = default_resolve_chat_model


def get_chat_model_resolver() -> ChatModelResolver:
    """FastAPI dependency hook. SaaS overlays substitute via
    ``app.dependency_overrides[get_chat_model_resolver] = ...``."""
    return _resolver


__all__ = [
    "ChatModelResolver",
    "default_resolve_chat_model",
    "get_chat_model_resolver",
]
