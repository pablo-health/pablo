# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the SOAP prompt's Objective-section guidance.

An audio-derived transcript has no visual channel, so the prompt must not
ask the model to report on appearance, eye contact, or posture — those
are exactly the kind of unsupported claims the source-attribution pass
later scores as unverified.
"""

import os

os.environ["ENVIRONMENT"] = "development"

from datetime import datetime

from app.models import Patient, Transcript
from app.notes.prompts.soap import build_soap_prompt


def _build_prompt() -> str:
    patient = Patient(
        id="p1",
        first_name="Jane",
        last_name="Doe",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        diagnosis="Generalized Anxiety Disorder",
    )
    transcript = Transcript(
        format="txt",
        content="[00:00] Therapist: How have you been?\n[00:05] Client: A bit tired.",
    )
    return build_soap_prompt(
        None, transcript, patient, datetime.fromisoformat("2024-06-01T00:00:00+00:00")
    )


def test_prompt_does_not_request_visual_observations() -> None:
    prompt = _build_prompt()

    assert "eye contact" not in prompt
    assert "posture" not in prompt
    assert "grooming, dress" not in prompt


def test_prompt_instructs_leaving_appearance_empty_without_verbal_evidence() -> None:
    prompt = _build_prompt()

    assert "Leave empty unless the transcript contains explicit verbal" in prompt


def test_prompt_hedges_inferred_affect_observations() -> None:
    prompt = _build_prompt()

    assert "Based on session content" in prompt
