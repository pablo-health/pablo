# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Chat and generation prompts — single source of truth.

The OSS distribution ships baseline prompts that are safe defaults for
self-hosters. Downstream consumers may register provider-aware overrides
via :func:`chat.register_provider` during their bootstrap. The
single-source-of-truth pattern lets the eval harness (``backend/evals``)
import the same prompt the production request path uses, so eval and prod
cannot drift.
"""

from . import chat

__all__ = ["chat"]
