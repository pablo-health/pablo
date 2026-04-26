# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note-type registry: schema-driven definitions for SOAP, Narrative, and overlays.

Adding a new note format is a registration call, not a refactor. Pablo
registers SOAP + Narrative at startup via :func:`register_builtin_note_types`.
Downstream overlays may register additional formats against the same
default registry at bootstrap.
"""

from .builtin import (
    NARRATIVE_DEFINITION,
    SOAP_DEFINITION,
    register_builtin_note_types,
)
from .registry import (
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
    "NoteFieldDef",
    "NoteFieldKind",
    "NoteSectionDef",
    "NoteTier",
    "NoteTypeDefinition",
    "NoteTypeRegistry",
    "get_default_registry",
    "register_builtin_note_types",
]
