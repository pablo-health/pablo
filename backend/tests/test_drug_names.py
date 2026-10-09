# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The transcriber's vocabulary from the chart, and the sound-alike guard after it.

No model runs here. The swap cases are the three pairs a transcriber was
measured writing for each other, each said by two speakers in a dose
sentence and a stop sentence: twelve sayings. Each is written here the way
it reaches the guard, synthetic text in the stored transcript's line format.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_proposals.drafting import parse_proposals
from app.drug_names.keyterms import KEYTERM_WORD_LIMIT, chart_keyterms
from app.drug_names.names import DATA_DIR, names_for, names_in, same_drug
from app.drug_names.sound_alikes import (
    SoundAlike,
    find_sound_alikes,
    known_pair_names,
    sounds_like,
    unconfirmed,
)
from app.models import Patient
from app.notes.chart_context import ChartContext, ChartMedication, chart_context_for
from app.notes.chart_fields import Statement, Statements, compose_all, rendered_fields
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.services.chart_field_extraction import extract_statements

NOW = datetime(2026, 10, 9, 15, tzinfo=UTC)

#: (said, written): the transcriber wrote ``written`` for ``said`` every time ``written``
#: was in its vocabulary.
PAIRS = [
    ("nortriptyline", "amitriptyline"),
    ("lorazepam", "clonazepam"),
    ("aripiprazole", "brexpiprazole"),
]
SPEAKERS = ("Clinician", "Client")
FRAMES = (
    "{Name} fifty milligrams every morning.",
    "I stopped the {name} two weeks ago.",
)


def _saying(speaker: str, frame: str, name: str) -> str:
    return "[00:05] " + speaker + ": " + frame.format(Name=name.capitalize(), name=name)


#: The twelve sayings, as (said, written, transcript line saying ``said``).
SWAP_CASES = [
    (said, written, _saying(speaker, frame, said))
    for said, written in PAIRS
    for speaker in SPEAKERS
    for frame in FRAMES
]
SWAP_IDS = [
    f"{said}-{speaker}-{kind}"
    for said, _ in PAIRS
    for speaker in SPEAKERS
    for kind in ("dose", "stop")
]


def _chart(*names: str, allergies: tuple[str, ...] = ()) -> ChartContext:
    return ChartContext(
        medications=tuple(ChartMedication(name, "1 mg") for name in names),
        allergy_status="recorded" if allergies else "not_recorded",
        allergies=tuple({"substance": a} for a in allergies),
    )


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------


def test_the_twelve_cases_are_the_measured_pairs_both_voices_both_frames() -> None:
    data = json.loads((DATA_DIR / "sound_alike_pairs.json").read_text())
    assert [(p["said"], p["written"]) for p in data["pairs"]] == PAIRS
    assert sum(p["sayings"] for p in data["pairs"]) == len(SWAP_CASES) == 12


def test_generic_and_brand_names_are_one_drug() -> None:
    assert names_for("sertraline") == ("sertraline", "Zoloft")
    assert names_for("ZOLOFT") == ("sertraline", "Zoloft")
    assert names_for("lithium carbonate ER")[0] == "lithium"
    assert names_for("an unlisted compound") == ("an unlisted compound",)
    assert same_drug("Ativan", "lorazepam")
    assert not same_drug("Ativan", "clonazepam")


def test_drug_names_are_found_in_a_line_longest_first() -> None:
    line = "Client: the Xanomeline and Trospium, and St. John's wort, not the sertraline."
    assert list(names_in(line)) == ["xanomeline and trospium", "St. John's wort", "sertraline"]
    assert list(names_in("Client: I slept well and took nothing.")) == []


# ---------------------------------------------------------------------------
# The distance rule, both directions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("said", "written"), PAIRS)
def test_each_measured_pair_sounds_alike_in_both_directions(said: str, written: str) -> None:
    assert sounds_like(said, written)
    assert sounds_like(written, said)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("clonidine", "clozapine"),  # 3 of 9 letters
        ("lamotrigine", "famotidine"),  # 3 of 11
        ("Restoril", "Zestril"),  # 2 of 8
        ("lorazepam", "diazepam"),  # 3 of 9
    ],
)
def test_names_within_a_third_of_each_other_sound_alike(a: str, b: str) -> None:
    assert sounds_like(a, b)
    assert sounds_like(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Xanax", "Lasix"),  # 3 of 5: a short name needs a closer match
        ("sertraline", "quetiapine"),
        ("Zoloft", "Zocor"),  # 3 of 6
        ("sertraline", "sertraline"),  # the same name is not a sound-alike
    ],
)
def test_names_further_apart_do_not(a: str, b: str) -> None:
    assert not sounds_like(a, b)
    assert not sounds_like(b, a)


# ---------------------------------------------------------------------------
# The guard on the twelve swap cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", SWAP_CASES, ids=SWAP_IDS)
def test_a_name_heard_against_its_listed_partner_is_marked(case: tuple[str, str, str]) -> None:
    """The chart lists the name the transcriber was pulled to; the visit says the other.
    The heard word is kept and marked against the chart's."""
    said, written, line = case
    found = find_sound_alikes(_chart(written), {4: line})
    assert found == (SoundAlike(heard=said, listed=written, segment_ids=(4,)),)
    assert found[0].mark == f"(heard as {said}; the chart lists {written})"


@pytest.mark.parametrize("case", SWAP_CASES, ids=SWAP_IDS)
def test_the_reverse_swap_is_marked_too(case: tuple[str, str, str]) -> None:
    """The chart lists the name said; the transcript wrote its partner."""
    said, written, line = case
    swapped = line.replace(said, written).replace(said.capitalize(), written.capitalize())
    found = find_sound_alikes(_chart(said), {4: swapped})
    assert [(f.heard, f.listed) for f in found] == [(written, said)]


@pytest.mark.parametrize("case", SWAP_CASES, ids=SWAP_IDS)
def test_a_name_the_chart_lists_is_not_marked(case: tuple[str, str, str]) -> None:
    said, written, line = case
    assert find_sound_alikes(_chart(said), {4: line}) == ()
    assert find_sound_alikes(_chart(said, written), {4: line}) == ()


@pytest.mark.parametrize("case", SWAP_CASES, ids=SWAP_IDS)
def test_no_proposal_comes_from_a_marked_name(case: tuple[str, str, str]) -> None:
    """An add of the heard name, or a stop of the listed one citing a line that names only
    the heard one, is not proposed. A typed document is not checked."""
    said, written, line = case
    chart = chart_context_for(
        Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW),
        [],
        [{"drug_name": written, "dose": "1 mg", "status": "active"}],
    )
    segments = {4: line}
    add = _change("add", said)
    stop = _change("stop", written)
    assert parse_proposals({"medication_changes": [add, stop]}, chart, segments) == []
    document = parse_proposals({"medication_changes": [add]}, chart, segments, origin="document")
    assert [p.item_key for p in document] == [said]


def _change(action: str, name: str, ids: tuple[int, ...] = (4,)) -> dict[str, Any]:
    return {
        "action": action,
        "drug_name": name,
        "dose": "1 mg",
        "what_changed": f"{action} {name}",
        "evidence_segment_ids": list(ids),
        "decided_by": "the clinician",
    }


def test_a_change_to_a_listed_name_said_as_listed_is_still_proposed() -> None:
    chart = _chart("clonazepam")
    segments = {1: "[00:05] Clinician: I'll stop the clonazepam today."}
    proposals = parse_proposals(
        {"medication_changes": [_change("stop", "clonazepam", (1,))]}, chart, segments
    )
    assert [(p.change.action, p.item_key) for p in proposals if p.change] == [
        ("stop", "clonazepam")
    ]


def test_an_add_of_a_drug_that_sounds_like_nothing_listed_is_still_proposed() -> None:
    chart = _chart("clonazepam")
    segments = {1: "[00:05] Client: My doctor started me on sertraline 50 mg."}
    proposals = parse_proposals(
        {"medication_changes": [_change("add", "sertraline", (1,))]}, chart, segments
    )
    assert [p.item_key for p in proposals] == ["sertraline"]
    assert unconfirmed("sertraline", [segments[1]], chart) is None


def test_a_brand_name_is_checked_as_its_drug() -> None:
    """Ativan is lorazepam, which sounds like the chart's clonazepam; Klonopin is the
    chart's own drug."""
    chart = _chart("clonazepam")
    assert [(f.heard, f.listed) for f in find_sound_alikes(chart, {1: "Client: the Ativan"})] == [
        ("Ativan", "clonazepam")
    ]
    assert find_sound_alikes(chart, {1: "Client: the Klonopin"}) == ()


def test_an_allergy_substance_is_on_the_chart_for_the_guard() -> None:
    chart = _chart(allergies=("clonazepam",))
    assert [f.listed for f in find_sound_alikes(chart, {1: "Client: lorazepam"})] == ["clonazepam"]


def test_a_name_the_extraction_read_is_checked_even_if_the_map_lacks_it() -> None:
    chart = _chart("Quelbrex")
    found = find_sound_alikes(chart, {1: "Client: the kelbrex"}, named=[("Kelbrex", (1,))])
    assert [(f.heard, f.listed, f.segment_ids) for f in found] == [("Kelbrex", "Quelbrex", (1,))]


def test_an_empty_chart_has_nothing_to_sound_like() -> None:
    assert find_sound_alikes(ChartContext(), {1: "Client: lorazepam"}) == ()


# ---------------------------------------------------------------------------
# The draft's medication list
# ---------------------------------------------------------------------------


def _follow_up() -> Any:
    raw = json.loads((TEMPLATES_DIR / "psychiatric_follow_up.json").read_text())["spec"]
    return to_definition("custom.follow_up", 1, PracticeNoteTypeSpec.model_validate(raw))


def _medications(chart: ChartContext, statements: Statements) -> list[str]:
    content = compose_all(_follow_up(), chart, {"place_of_service": "In office"}, statements)
    return content["medications"]["current_medications"]  # type: ignore[no-any-return]


LORAZEPAM = SoundAlike(heard="lorazepam", listed="clonazepam", segment_ids=(2,))


def test_a_stated_medication_that_sounds_like_a_listed_one_carries_the_mark() -> None:
    said = Statements(
        fields=(
            Statement(
                "current_medications",
                stated="I take lorazepam 1 mg at night",
                medication="lorazepam",
                dose="1 mg",
                segment_ids=(2,),
            ),
        ),
        sound_alikes=(LORAZEPAM,),
    )
    assert _medications(_chart("clonazepam"), said) == [
        "clonazepam 1 mg",
        '(stated this visit: "I take lorazepam 1 mg at night") '
        "(heard as lorazepam; the chart lists clonazepam)",
    ]


def test_a_sound_alike_no_statement_names_is_an_item_of_its_own() -> None:
    """The extraction read the line as the listed drug, or left it out: the mark stands."""
    said = Statements(
        fields=(
            Statement("current_medications", stated="omeprazole 20 mg", medication="omeprazole"),
        ),
        sound_alikes=(LORAZEPAM,),
    )
    assert _medications(_chart("clonazepam"), said) == [
        "clonazepam 1 mg",
        '(stated this visit: "omeprazole 20 mg")',
        "(heard as lorazepam; the chart lists clonazepam)",
    ]


def test_without_a_sound_alike_the_list_is_as_before() -> None:
    said = Statements(
        fields=(
            Statement("current_medications", stated="omeprazole 20 mg", medication="omeprazole"),
        )
    )
    assert _medications(_chart("clonazepam"), said) == [
        "clonazepam 1 mg",
        '(stated this visit: "omeprazole 20 mg")',
    ]


def test_the_extraction_finds_the_sound_alikes_beside_its_reply() -> None:
    fields = rendered_fields(_follow_up())
    transcript = "\n".join(
        [
            "[00:01] Clinician: How is the clonazepam?",
            "[00:05] Client: I've been taking the lorazepam at night, 1 mg.",
        ]
    )
    reply = {
        "statements": [
            {
                "field_key": "current_medications",
                "screen": "stated",
                "stated": "taking the lorazepam at night, 1 mg",
                "medication": "lorazepam",
                "dose": "1 mg",
                "evidence_segment_ids": [1],
            }
        ]
    }
    statements = extract_statements(
        lambda *_: reply, fields, _chart("clonazepam"), {"place_of_service": "x"}, transcript
    )
    assert statements.sound_alikes == (
        SoundAlike(heard="lorazepam", listed="clonazepam", segment_ids=(1,)),
    )
    assert _medications(_chart("clonazepam"), statements)[1].endswith(LORAZEPAM.mark)


def test_a_note_without_a_medication_list_reads_for_no_sound_alikes() -> None:
    fields = [f for f in rendered_fields(_follow_up()) if f.source != "medications"]
    statements = extract_statements(
        lambda *_: {"statements": []},
        fields,
        _chart("clonazepam"),
        {"place_of_service": "x"},
        "[00:05] Client: the lorazepam",
    )
    assert statements.sound_alikes == ()


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_the_vocabulary_is_every_name_for_each_chart_drug_once() -> None:
    assert chart_keyterms(["Sertraline", "Zoloft", "bupropion XL", "penicillin"]) == [
        "sertraline",
        "Zoloft",
        "bupropion",
        "Wellbutrin",
        "penicillin",
    ]


def test_a_name_the_map_lacks_is_sent_as_the_chart_writes_it() -> None:
    assert chart_keyterms(["Quelbrex", " ", ""]) == ["Quelbrex"]


def test_an_empty_chart_sends_no_vocabulary() -> None:
    assert chart_keyterms([]) == []


@pytest.mark.parametrize("name", sorted(n for pair in PAIRS for n in pair))
def test_a_name_of_a_measured_pair_is_never_sent(name: str) -> None:
    """Listed, it pulls its partner onto itself; its brand is still sent."""
    terms = chart_keyterms([name])
    assert name not in terms
    assert terms == [n for n in names_for(name) if n != name]
    assert not set(terms) & known_pair_names()


def test_the_vocabulary_stops_at_the_word_cap_in_chart_order() -> None:
    chart = [f"compound{i}" for i in range(KEYTERM_WORD_LIMIT - 1)] + ["two words", "last"]
    terms = chart_keyterms(chart)
    assert sum(len(t.split()) for t in terms) == KEYTERM_WORD_LIMIT - 1
    assert terms[-1] == f"compound{KEYTERM_WORD_LIMIT - 2}"
