# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The scoring maths of scripts/transcription_benchmark.py: WER and attribution.

The script runs in the transcription worker's environment, but its scoring
imports nothing beyond the standard library, so it loads here by path.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "transcription_benchmark.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("transcription_benchmark", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through it
    spec.loader.exec_module(module)
    return module


bench = _load()
Word = bench.Word

TURNS = [
    {"speaker": "Therapist", "start": 1.0, "end": 3.0, "text": "How was your week?"},
    {"speaker": "Client", "start": 3.5, "end": 6.0, "text": "It was busy, I didn't sleep."},
]


def test_normalize_drops_punctuation_and_case_but_keeps_contractions() -> None:
    assert bench.normalize_words("It was busy, I DIDN'T sleep.") == [
        "it",
        "was",
        "busy",
        "i",
        "didn't",
        "sleep",
    ]
    assert bench.normalize_words("well-being 'quoted'") == ["well", "being", "quoted"]


@pytest.mark.parametrize(
    ("ref", "hyp", "expected"),
    [
        ("a b c", "a b c", 0),
        ("a b c", "a x c", 1),  # substitution
        ("a b c", "a c", 1),  # deletion
        ("a b c", "a b b c", 1),  # insertion
        ("a b c", "", 3),
        ("", "a b", 2),
        ("a b c d", "b c d a", 2),  # a moved: one deletion, one insertion
    ],
)
def test_edit_distance(ref: str, hyp: str, expected: int) -> None:
    assert bench.edit_distance(ref.split(), hyp.split()) == expected


def test_perfect_transcript_scores_zero_wer_and_full_attribution() -> None:
    segments = [
        Word(1.1, 2.9, "Therapist", "How was your week?"),
        Word(3.6, 5.9, "Client", "It was busy, I didn't sleep."),
    ]
    score = bench.score_session(TURNS, segments)
    assert (score.ref_words, score.edits) == (10, 0)
    assert score.wer == 0.0
    assert (score.attributed_right, score.attributed_wrong, score.unmatched) == (10, 0, 0)
    assert score.attribution == 1.0


def test_wer_is_per_channel_so_bleed_costs_insertions() -> None:
    # The client's words also came out of the therapist's track (bleed).
    segments = [
        Word(1.1, 2.9, "Therapist", "How was your week?"),
        Word(3.6, 5.9, "Therapist", "It was busy"),
        Word(3.6, 5.9, "Client", "It was busy, I didn't sleep."),
    ]
    score = bench.score_session(TURNS, segments)
    assert score.edits == 3  # three words inserted on the therapist channel
    assert score.wer == pytest.approx(3 / 10)
    assert (score.attributed_right, score.attributed_wrong) == (10, 3)
    assert score.attribution == pytest.approx(10 / 13)


def test_attribution_follows_the_words_not_the_segment_timing() -> None:
    # One therapist segment whose timing spans the client's turn (the
    # therapist's track is silent there): every word is still the therapist's.
    segments = [Word(1.0, 6.0, "Therapist", "How was your week?")]
    score = bench.score_session(TURNS, segments)
    assert (score.attributed_right, score.attributed_wrong, score.unmatched) == (4, 0, 0)


def test_a_word_both_speakers_said_counts_for_the_channel_speaker() -> None:
    # "was" is in both turns; on the client channel it aligns to the client's.
    segments = [Word(3.6, 5.9, "Client", "was")]
    score = bench.score_session(TURNS, segments)
    assert (score.attributed_right, score.attributed_wrong) == (1, 0)


def test_words_matching_nothing_are_unmatched_not_attributed() -> None:
    segments = [Word(7.0, 8.0, "Client", "thank you for watching")]
    score = bench.score_session(TURNS, segments)
    assert score.unmatched == 4
    assert (score.attributed_right, score.attributed_wrong) == (0, 0)
    assert score.attribution == 0.0
    # Therapist: four words deleted. Client: four substituted, two deleted.
    assert score.edits == 10


@pytest.mark.parametrize(
    ("ref", "hyp", "expected"),
    [
        ("a b c", "a b c", [(0, 0), (1, 1), (2, 2)]),
        ("a b c", "x b y", [(1, 1)]),
        ("a b c d", "b d", [(1, 0), (3, 1)]),
        ("a b", "", []),
    ],
)
def test_matched_pairs(ref: str, hyp: str, expected: list[tuple[int, int]]) -> None:
    assert bench.matched_pairs(ref.split(), hyp.split()) == expected


def test_a_preference_breaks_ties_but_never_costs_a_match() -> None:
    ref = ["a", "b", "a"]
    assert bench.matched_pairs(ref, ["a"], [False, False, True]) == [(2, 0)]
    # Preferring the last "a" would lose the "b" match: the count wins.
    assert bench.matched_pairs(ref, ["a", "b"], [False, False, True]) == [(0, 0), (1, 1)]


def test_scores_pool_as_sums_not_averaged_rates() -> None:
    total = bench.Score()
    total.add(bench.Score(ref_words=10, edits=1, attributed_right=9, attributed_wrong=1))
    total.add(bench.Score(ref_words=90, edits=29, attributed_right=81, attributed_wrong=9))
    assert total.wer == pytest.approx(30 / 100)  # not mean(10%, 32%)
    assert total.attribution == pytest.approx(90 / 100)


def test_table_reports_rates_and_marks_a_failed_model() -> None:
    ok = bench.Result(
        model="tiny",
        score=bench.Score(ref_words=100, edits=12, attributed_right=95, attributed_wrong=5),
        audio_seconds=120.0,
        transcribe_seconds=30.0,
        load_seconds=1.3,
        peak_rss_mb=512.4,
    )
    failed = bench.Result(model="medium", error="killed by signal 9 (out of memory?)")
    provider = bench.Result(
        model=bench.PROVIDER_ROW,
        score=bench.Score(ref_words=100, edits=5, attributed_right=100),
        audio_seconds=120.0,
    )
    lines = bench.render_table([ok, failed, provider]).splitlines()
    assert lines[2] == "| tiny | 12.0% | 95.0% | 0 | 15.0 | 1.3 | 512 |"
    assert lines[3].startswith("| medium | failed: killed by signal 9")
    assert lines[4] == "| default provider | 5.0% | 100.0% | 0 | n/a | n/a | n/a |"
