# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The AI features a deployment can give fallback models, by key.

Each call site names its feature, and ``Settings.ai_fallbacks`` maps a key
to the models that feature may fall back to. A key with no entry has no
fallback. Every key here is described on ``Settings.ai_fallbacks``.
"""

from __future__ import annotations

from enum import StrEnum


class AIFeature(StrEnum):
    NOTE_GENERATION = "note_generation"
    NOTE_IMPORT = "note_import"
    NOTE_TYPE_DERIVE = "note_type_derive"
    AVAILABILITY_PARSE = "availability_parse"
    CHAT = "chat"
    PATIENT_CHAT = "patient_chat"


__all__ = ["AIFeature"]
