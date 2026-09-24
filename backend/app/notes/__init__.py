# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: schema-driven definitions for every note format.

Adding a new note format is a registration call, not a refactor. Pablo
registers its built-in formats at startup via
:func:`register_builtin_note_types`. Downstream consumers may register
additional formats against the same default registry at bootstrap.
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
    NoteContext,
    NoteFieldDef,
    NoteFieldKind,
    NoteSectionDef,
    NoteTier,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
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
    "SAFETY_PLAN_DEFINITION",
    "SOAP_DEFINITION",
    "TREATMENT_PLAN_DEFINITION",
    "NoteContext",
    "NoteFieldDef",
    "NoteFieldKind",
    "NoteSectionDef",
    "NoteTier",
    "NoteTypeAuthorizer",
    "NoteTypeDefinition",
    "NoteTypeRegistry",
    "get_default_registry",
    "get_note_type_authorizer",
    "register_builtin_note_types",
]
