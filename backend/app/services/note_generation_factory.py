# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The note generation service the app drafts with, built the same way everywhere.

Under the end-to-end stack the drafts come from its stand-in (the setting
refuses to load outside development), so a spec sees the same draft every
run. The stand-in answers every model, fallbacks included, so the fallback
order runs there as it does against real providers. A request builds it
through ``app.routes.notes.get_note_generation_service``; a job, with no
request to depend on, builds it here.
"""

from __future__ import annotations

from ..settings import get_settings
from .ai_features import AIFeature
from .hedged_structured_llm_gateway import generation_gateway
from .http_structured_llm_gateway import HttpStructuredLLMGateway
from .note_generation_service import NoteGenerationService, RegistryNoteGenerationService


def note_generation_service() -> NoteGenerationService:
    """The configured note generation service: the stand-in when one is set, else the real one."""
    base_url = get_settings().note_generation_base_url
    if base_url:
        stand_in = generation_gateway(AIFeature.NOTE_GENERATION, HttpStructuredLLMGateway(base_url))
        return RegistryNoteGenerationService(llm_gateway=stand_in)
    return RegistryNoteGenerationService()
