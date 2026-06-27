# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the in-tree transcript normalizer.

The normalizer replaces what the legacy ``meeting_transcription`` package
used to do for SOAP generation. We cover each supported format plus the
error path. Behavior we care about: all four formats produce the
canonical ``[MM:SS] Speaker: text`` line shape that
:func:`source_attribution_service.format_transcript_with_segment_ids`
consumes downstream.
"""

from __future__ import annotations

import json

import pytest

from backend.app.notes.transcript_normalize import (
    normalize_transcript_to_canonical_lines,
)


class TestTxtFormat:
    def test_passthrough_drops_blank_lines(self) -> None:
        content = "[00:01] Therapist: Hello\n\n[00:05] Client: Hi\n"
        result = normalize_transcript_to_canonical_lines(content, "txt")
        assert result == "[00:01] Therapist: Hello\n[00:05] Client: Hi"

    def test_empty_string(self) -> None:
        assert normalize_transcript_to_canonical_lines("", "txt") == ""


class TestVttFormat:
    def test_zoom_style_vtt(self) -> None:
        content = (
            "WEBVTT\n"
            "\n"
            "00:00:05.000 --> 00:00:08.500\n"
            "Sarah Chen: Good morning everyone\n"
            "\n"
            "00:00:09.000 --> 00:00:12.000\n"
            "John Doe: Glad to be here\n"
        )
        result = normalize_transcript_to_canonical_lines(content, "vtt")
        assert result == (
            "[00:05] Sarah Chen: Good morning everyone\n[00:09] John Doe: Glad to be here"
        )

    def test_vtt_multi_line_cue(self) -> None:
        content = (
            "WEBVTT\n"
            "\n"
            "00:00:05.000 --> 00:00:10.000\n"
            "Sarah Chen: Good morning\n"
            "everyone, glad you could come\n"
        )
        result = normalize_transcript_to_canonical_lines(content, "vtt")
        assert result == "[00:05] Sarah Chen: Good morning everyone, glad you could come"

    def test_vtt_continuation_reuses_prior_speaker(self) -> None:
        content = (
            "WEBVTT\n"
            "\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "Sarah Chen: First sentence\n"
            "\n"
            "00:00:09.000 --> 00:00:12.000\n"
            "second sentence with no speaker prefix\n"
        )
        result = normalize_transcript_to_canonical_lines(content, "vtt")
        # The second cue has no speaker prefix → reuse "Sarah Chen".
        assert result == (
            "[00:05] Sarah Chen: First sentence\n"
            "[00:09] Sarah Chen: second sentence with no speaker prefix"
        )

    def test_vtt_hour_rolls_into_minutes(self) -> None:
        content = "WEBVTT\n\n01:02:03.000 --> 01:02:05.000\nSpeaker: At one hour\n"
        result = normalize_transcript_to_canonical_lines(content, "vtt")
        assert result == "[62:03] Speaker: At one hour"


class TestGoogleMeetFormat:
    def test_bracketed_timestamp(self) -> None:
        content = "[00:00:08]\nSarah Chen: Hello\n[00:00:14]\nJohn Doe: Hi there\n"
        result = normalize_transcript_to_canonical_lines(content, "google_meet")
        assert result == ("[00:08] Sarah Chen: Hello\n[00:14] John Doe: Hi there")

    def test_speaker_continuation(self) -> None:
        content = (
            "[00:00:08]\n"
            "Sarah Chen: First sentence.\n"
            "More from same speaker.\n"
            "[00:00:20]\n"
            "John Doe: My turn\n"
        )
        result = normalize_transcript_to_canonical_lines(content, "google_meet")
        assert result == (
            "[00:08] Sarah Chen: First sentence. More from same speaker.\n[00:20] John Doe: My turn"
        )

    def test_mm_ss_timestamp_form(self) -> None:
        # Some exporters use [MM:SS] instead of [HH:MM:SS].
        content = "[01:23]\nSpeaker: Hello\n"
        result = normalize_transcript_to_canonical_lines(content, "google_meet")
        assert result == "[01:23] Speaker: Hello"


class TestJsonFormat:
    def test_combined_transcript_shape(self) -> None:
        content = json.dumps(
            [
                {
                    "participant": {"name": "Sarah Chen"},
                    "text": "Hello everyone",
                    "start_timestamp": {"relative": 5.0},
                },
                {
                    "participant": {"name": "John Doe"},
                    "text": "Glad to be here",
                    "start_timestamp": {"relative": 9.0},
                },
            ]
        )
        result = normalize_transcript_to_canonical_lines(content, "json")
        assert result == ("[00:05] Sarah Chen: Hello everyone\n[00:09] John Doe: Glad to be here")

    def test_plain_segment_shape(self) -> None:
        content = json.dumps(
            [
                {"speaker": "Sarah", "text": "Hi", "start": 0},
                {"speaker": "John", "text": "Hello", "start": 65.5},
            ]
        )
        result = normalize_transcript_to_canonical_lines(content, "json")
        assert result == "[00:00] Sarah: Hi\n[01:05] John: Hello"

    def test_skips_empty_text(self) -> None:
        content = json.dumps(
            [
                {"speaker": "Sarah", "text": "", "start": 0},
                {"speaker": "John", "text": "Hello", "start": 5},
            ]
        )
        result = normalize_transcript_to_canonical_lines(content, "json")
        assert result == "[00:05] John: Hello"

    def test_rejects_non_list_top_level(self) -> None:
        content = json.dumps({"not": "a list"})
        with pytest.raises(ValueError, match="must be a list"):
            normalize_transcript_to_canonical_lines(content, "json")

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(ValueError, match="not valid JSON"):
            normalize_transcript_to_canonical_lines("{ not json", "json")


class TestUnsupportedFormat:
    def test_raises_on_unknown_format(self) -> None:
        with pytest.raises(ValueError, match="Unsupported transcript format"):
            normalize_transcript_to_canonical_lines("hello", "srt")
