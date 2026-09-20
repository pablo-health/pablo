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

A question can be asked only when an earlier answer calls for it.
:mod:`app.intake.rules` says what such a condition may look like and
refuses one that cannot be published; :mod:`app.intake.visibility`
evaluates it, and the browser runs a port of that function against the same
fixtures so the two cannot drift.

A consent item is answered by signing rather than by sending a value, and
three small modules carry what that record is made of:
:mod:`app.intake.documents` reduces the words to a digest,
:mod:`app.intake.consent_statement` holds the versioned sentence somebody
agrees under, and :mod:`app.intake.signatures` folds a stored signature's
evidence into one string that can be checked against the row later.
"""

from .answers import SIGNED_ITEM_TYPES, AnswerError, is_answered, validate_answer
from .completion import Completion, CompletionItem, assess
from .consent_statement import (
    CONSENT_STATEMENTS,
    CURRENT_CONSENT_STATEMENT_VERSION,
    consent_statement,
)
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
from .signatures import EVIDENCE_FIELDS, evidence_digest
from .visibility import VisibilityItem, evaluate

__all__ = [
    "CONSENT_STATEMENTS",
    "CURRENT_CONSENT_STATEMENT_VERSION",
    "DISPLAY_ONLY_ITEM_TYPES",
    "EVIDENCE_FIELDS",
    "ITEM_TYPES",
    "SIGNED_ITEM_TYPES",
    "AnswerError",
    "Completion",
    "CompletionItem",
    "ItemConfig",
    "ItemConfigError",
    "ItemDraft",
    "VisibilityItem",
    "VisibleWhen",
    "assess",
    "consent_statement",
    "evaluate",
    "evidence_digest",
    "is_answered",
    "validate_answer",
    "validate_item_config",
    "validate_item_list",
]
