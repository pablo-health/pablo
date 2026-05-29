# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for transcription_queue_service helpers."""

from __future__ import annotations

import pytest
from app.services.transcription_queue_service import _safe_audio_extension


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("recording.wav", "wav"),
        ("RECORDING.WAV", "wav"),  # lowercased
        ("clip.mp3", "mp3"),
        ("audio.webm", "webm"),
        ("noextension", "wav"),  # no dot -> default
        ("trailingdot.", "wav"),  # empty ext -> default
        ('rec.wav","injected":"x', "wav"),  # quote/comma injection -> rejected
        ("rec.a/../../etc/passwd", "wav"),  # slashes -> rejected
        ("rec.toolongext", "wav"),  # > 5 chars -> rejected
        ("rec.m4a", "m4a"),
    ],
)
def test_safe_audio_extension(filename: str, expected: str) -> None:
    assert _safe_audio_extension(filename) == expected
