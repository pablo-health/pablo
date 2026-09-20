# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake packets — the form a practice builds and a patient fills in.

A packet is a named template with numbered versions. A version holds an
ordered list of items, each item one question or one piece of display text.
Publishing a version freezes it; everything a patient ever answered can be
read back against the exact list of questions they were asked.

:mod:`app.intake.items` is the vocabulary — what kinds of item exist, what
each one's configuration has to look like, and which of them a rule may refer
to. Everything else in the feature reads that module rather than re-deciding
what a valid item is.
"""

from .items import (
    DISPLAY_ONLY_ITEM_TYPES,
    ITEM_TYPES,
    ItemConfig,
    ItemConfigError,
    ItemDraft,
    VisibleWhen,
    validate_item_config,
    validate_item_list,
)

__all__ = [
    "DISPLAY_ONLY_ITEM_TYPES",
    "ITEM_TYPES",
    "ItemConfig",
    "ItemConfigError",
    "ItemDraft",
    "VisibleWhen",
    "validate_item_config",
    "validate_item_list",
]
