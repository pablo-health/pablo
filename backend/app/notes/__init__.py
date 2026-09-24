# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: schema-driven definitions for every note format.

Adding a new note format is a registration call, not a refactor. Pablo
registers its built-in formats at startup via
:func:`register_builtin_note_types`. A practice can add its own formats
without a deploy; those are resolved per practice (see
:mod:`.practice_types`).
"""

from .authorizer import NoteTypeAuthorizer, get_note_type_authorizer
from .builtin import (
    BIRP_DEFINITION,
    BUILTIN_NOTE_DEFINITIONS,
    DAP_DEFINITION,
    GIRP_DEFINITION,
    INTAKE_DEFINITION,
    MEDICATIONS_DEFINITION,
    MEETING_SUMMARY_DEFINITION,
    NARRATIVE_DEFINITION,
    SAFETY_PLAN_DEFINITION,
    SOAP_DEFINITION,
    TREATMENT_PLAN_DEFINITION,
    register_builtin_note_types,
)
from .registry import (
    PRACTICE_KEY_PREFIX,
    NoteContext,
    NoteFieldDef,
    NoteFieldKind,
    NoteInputDef,
    NoteInputKind,
    NoteSectionDef,
    NoteTier,
    NoteTypeDefinition,
    NoteTypeRegistry,
    PracticeNoteTypeSource,
    get_default_registry,
    is_practice_key,
)

__all__ = [
    "BIRP_DEFINITION",
    "BUILTIN_NOTE_DEFINITIONS",
    "DAP_DEFINITION",
    "GIRP_DEFINITION",
    "INTAKE_DEFINITION",
    "MEDICATIONS_DEFINITION",
    "MEETING_SUMMARY_DEFINITION",
    "NARRATIVE_DEFINITION",
    "PRACTICE_KEY_PREFIX",
    "SAFETY_PLAN_DEFINITION",
    "SOAP_DEFINITION",
    "TREATMENT_PLAN_DEFINITION",
    "NoteContext",
    "NoteFieldDef",
    "NoteFieldKind",
    "NoteInputDef",
    "NoteInputKind",
    "NoteSectionDef",
    "NoteTier",
    "NoteTypeAuthorizer",
    "NoteTypeDefinition",
    "NoteTypeRegistry",
    "PracticeNoteTypeSource",
    "get_default_registry",
    "get_note_type_authorizer",
    "is_practice_key",
    "register_builtin_note_types",
]
