# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: schema-driven definitions for SOAP, Narrative, and overlays.

Adding a new note format is a registration call, not a refactor. Pablo
registers SOAP + Narrative at startup via :func:`register_builtin_note_types`.
Downstream overlays may register additional formats against the same
default registry at bootstrap.
"""

from .authorizer import NoteTypeAuthorizer, get_note_type_authorizer
from .builtin import (
    NARRATIVE_DEFINITION,
    SOAP_DEFINITION,
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
    "NARRATIVE_DEFINITION",
    "SOAP_DEFINITION",
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
