# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the chat context bundler."""

from datetime import UTC, datetime

import pytest
from app.models.note import Note
from app.repositories import InMemoryNotesRepository
from app.services.chat_context_bundler import (
    DEFAULT_SOURCE_SELECTION,
    BundlerDeps,
    ContextOverflowError,
    assemble_context_bundle,
    estimate_tokens,
)


def _make_note(idx: int, body: str) -> Note:
    return Note(
        id=f"note-{idx}",
        patient_id="pat-1",
        note_type="soap",
        created_at=datetime(2026, 1, idx, tzinfo=UTC),
        updated_at=datetime(2026, 1, idx, tzinfo=UTC),
        content={"subjective": {"narrative": body}},
        finalized_at=datetime(2026, 1, idx, tzinfo=UTC),
    )


def test_pasted_text_is_included_with_header() -> None:
    repo = InMemoryNotesRepository()
    bundle = assemble_context_bundle(
        deps=BundlerDeps(notes_repo=repo),
        patient_id="pat-1",
        selection={"pasted_text": {"content": "external context body"}},
        token_budget=10_000,
    )
    assert "USER-PASTED EXTERNAL DOCUMENT" in bundle.text
    assert "external context body" in bundle.text
    keys = [s["source_key"] for s in bundle.sources_included]
    assert "pasted_text" in keys


def test_progress_notes_recent_includes_in_order() -> None:
    repo = InMemoryNotesRepository()
    repo.add(_make_note(1, "older note body"))
    repo.add(_make_note(5, "newer note body"))
    bundle = assemble_context_bundle(
        deps=BundlerDeps(notes_repo=repo),
        patient_id="pat-1",
        selection={"progress_notes_recent": {"limit": 2}},
        token_budget=10_000,
    )
    notes_manifest = next(
        s for s in bundle.sources_included if s["source_key"] == "progress_notes_recent"
    )
    assert notes_manifest["note_ids"] == ["note-5", "note-1"]
    assert "older note body" in bundle.text
    assert "newer note body" in bundle.text


def test_pasted_text_overflow_raises() -> None:
    repo = InMemoryNotesRepository()
    huge = "x" * 80_000
    with pytest.raises(ContextOverflowError):
        assemble_context_bundle(
            deps=BundlerDeps(notes_repo=repo),
            patient_id="pat-1",
            selection={"pasted_text": {"content": huge}},
            token_budget=100,
        )


def test_default_selection_drops_unavailable_sources_into_manifest() -> None:
    repo = InMemoryNotesRepository()
    bundle = assemble_context_bundle(
        deps=BundlerDeps(notes_repo=repo),
        patient_id="pat-1",
        selection=DEFAULT_SOURCE_SELECTION,
        token_budget=10_000,
    )
    statuses = {
        s["source_key"]: s.get("status")
        for s in bundle.sources_included
    }
    # Sources whose backing data doesn't yet exist record an
    # ``unavailable`` status rather than silently disappearing.
    assert statuses.get("current_medications") == "unavailable"
    assert statuses.get("safety_plan_active") == "unavailable"


def test_manifest_is_phi_free() -> None:
    repo = InMemoryNotesRepository()
    repo.add(_make_note(1, "patient describes anxiety"))
    bundle = assemble_context_bundle(
        deps=BundlerDeps(notes_repo=repo),
        patient_id="pat-1",
        selection={"progress_notes_recent": {"limit": 1}},
        token_budget=10_000,
    )
    manifest = bundle.manifest()
    serialized = str(manifest)
    assert "anxiety" not in serialized
    assert "patient describes" not in serialized


def test_estimate_tokens_is_at_least_one() -> None:
    assert estimate_tokens("") >= 1
    assert estimate_tokens("hi") >= 1
    assert estimate_tokens("a" * 100) >= 25
