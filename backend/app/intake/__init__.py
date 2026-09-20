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

:mod:`app.intake.answers` is the other half of it: what answering each kind
of question means. :mod:`app.intake.completion` puts the two together and
says whether a form is finished, which is the one question no client is
allowed to answer for itself.
"""

from .answers import AnswerError, is_answered, validate_answer
from .completion import Completion, CompletionItem, assess, every_item_visible
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
    "AnswerError",
    "Completion",
    "CompletionItem",
    "ItemConfig",
    "ItemConfigError",
    "ItemDraft",
    "VisibleWhen",
    "assess",
    "every_item_visible",
    "is_answered",
    "validate_answer",
    "validate_item_config",
    "validate_item_list",
]
