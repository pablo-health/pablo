# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The listed medications a visit may have changed, found before the proposal call runs.

A stopped medication the call never proposes stays on the list as one the client
takes. These pin the deterministic read that puts such a medication to the call to
decide, and that a reply naming it with its dose still reaches the listed row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.chart_proposals.drafting import build_prompt, parse_proposals, propose_chart_updates
from app.chart_proposals.families import MEDICATIONS
from app.chart_proposals.medication_mentions import medications_to_decide
from app.chart_proposals.models import MedicationKept
from app.models import Patient, Transcript
from app.notes.chart_context import ChartContext, chart_context_for


def _chart(*meds: tuple[str, str, str | None]) -> ChartContext:
    now = datetime.now(UTC)
    patient = Patient(id="p", first_name="Sam", last_name="Sample", created_at=now, updated_at=now)
    rows = [
        {"drug_name": name, "dose": dose, "frequency": frequency, "status": "active"}
        for name, dose, frequency in meds
    ]
    return chart_context_for(patient, [], rows)


CHART = _chart(
    ("escitalopram", "10 mg", "every morning"),
    ("trazodone", "50 mg", "at bedtime"),
    ("lamotrigine", "150 mg", "every morning"),
    ("lithium carbonate ER", "900 mg", "at bedtime"),
    ("clonazepam", "0.5 mg", "twice daily"),
)


def _segments(*lines: str) -> dict[int, str]:
    return dict(enumerate(lines))


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        (
            ["Therapist: I'd like to take the escitalopram from ten to twenty milligrams."],
            ["escitalopram"],
        ),
        (["Therapist: Lamotrigine 125 mg every morning, not 150."], ["lamotrigine"]),
        (["Therapist: Let's go to lamotrigine one twenty-five milligrams."], ["lamotrigine"]),
        (
            ["Therapist: Lithium down to six hundred milligrams at bedtime."],
            ["lithium carbonate ER"],
        ),
        (["Therapist: Clonazepam point two five milligrams at bedtime."], ["clonazepam"]),
        (["Client: I stopped the trazodone in February."], ["trazodone"]),
        (["Client: I ran out of trazodone a month ago."], ["trazodone"]),
        (
            [
                "Therapist: Are you still taking the trazodone?",
                "Client: I haven't taken it since March.",
            ],
            ["trazodone"],
        ),
        (["Client: I quit the trazodone, it made me groggy."], ["trazodone"]),
    ],
    ids=[
        "spoken-new-dose",
        "written-new-dose",
        "one-twenty-five",
        "part-of-the-listed-name",
        "point-two-five",
        "stopped",
        "ran-out",
        "answer-in-the-next-line",
        "quit",
    ],
)
def test_a_listed_medication_named_with_another_dose_or_a_stop_word_is_put_to_the_call(
    lines: list[str], expected: list[str]
) -> None:
    assert medications_to_decide(CHART, _segments(*lines)) == expected


@pytest.mark.parametrize(
    "lines",
    [
        ["Therapist: Escitalopram ten every morning, any missed doses?", "Client: No."],
        ["Therapist: Lamotrigine one fifty milligrams, same as before."],
        ["Therapist: Clonazepam zero point five milligrams twice daily, unchanged."],
        ["Client: The escitalopram is fine. Two Saturdays ago I slept in."],
        ["Client: My primary care started me on amlodipine 5 mg, and I stopped the ibuprofen."],
        ["Client: Things are about the same.", "Therapist: Okay."],
    ],
    ids=[
        "same-dose-spoken",
        "same-dose-one-fifty",
        "same-dose-decimal",
        "a-number-that-is-not-a-dose",
        "not-on-the-list",
        "not-named",
    ],
)
def test_a_medication_taken_as_listed_or_not_on_the_list_is_not(lines: list[str]) -> None:
    assert medications_to_decide(CHART, _segments(*lines)) == []


def test_the_prompt_lists_the_medications_to_decide_and_where_a_kept_one_goes() -> None:
    prompt = build_prompt(CHART, "[S0] hello", to_decide=["trazodone"])

    assert "Decide each one" in prompt
    assert "medications_kept" in prompt
    assert "\n- trazodone\n" in prompt
    assert "Decide each one" not in build_prompt(CHART, "[S0] hello")


TRANSCRIPT = Transcript(
    format="txt",
    content="\n".join(
        [
            "[00:01] Therapist: Are you taking the trazodone?",
            "[00:05] Client: No, I stopped it in February, it made me groggy.",
            "[00:09] Therapist: And the escitalopram, let's go from ten to twenty milligrams.",
            "[00:14] Client: The lamotrigine, I ran out last week but I picked it up yesterday.",
        ]
    ),
)


def _call(reply: dict[str, Any]) -> tuple[list[str], Any]:
    prompts: list[str] = []

    def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        prompts.append(user)
        assert "medications_kept" in schema["properties"]
        return reply

    return prompts, propose_chart_updates(complete, CHART, TRANSCRIPT)


def test_each_medication_put_to_the_call_is_proposed_or_kept_with_a_reason() -> None:
    reply = {
        "proposals": [],
        "medication_changes": [
            {
                "action": "stop",
                "drug_name": "trazodone",
                "reason": "morning grogginess; stopped in February",
                "what_changed": "Stopped",
                "evidence_segment_ids": [1],
            },
            {
                "action": "change",
                "drug_name": "escitalopram 10 mg",
                "dose": "20 mg",
                "what_changed": "Increased",
                "evidence_segment_ids": [2],
            },
        ],
        "medications_kept": [
            {"drug_name": "lamotrigine 150 mg", "reason": "Ran out briefly; taking it again."}
        ],
    }

    prompts, drafted = _call(reply)

    assert "- escitalopram\n- trazodone\n- lamotrigine\n" in prompts[0]
    assert [(p.item_key, p.proposed_text) for p in drafted.proposals] == [
        ("trazodone", "Stopped: morning grogginess; stopped in February"),
        ("escitalopram", "escitalopram 20 mg, every morning"),
    ]
    assert drafted.to_decide == ("escitalopram", "trazodone", "lamotrigine")
    assert drafted.kept == (MedicationKept("lamotrigine", "Ran out briefly; taking it again."),)


def test_one_the_call_neither_proposed_nor_explained_is_kept_with_no_reason() -> None:
    _, drafted = _call({"proposals": [], "medication_changes": [], "medications_kept": []})

    assert drafted.kept == (
        MedicationKept("escitalopram", ""),
        MedicationKept("trazodone", ""),
        MedicationKept("lamotrigine", ""),
    )


def _change(name: str, chart: ChartContext) -> list[Any]:
    item = {
        "action": "change",
        "drug_name": name,
        "dose": "20 mg",
        "what_changed": "Increased",
        "evidence_segment_ids": [0],
    }
    reply = {"proposals": [], "medication_changes": [item]}
    return parse_proposals(reply, chart, {0: "Therapist: twenty milligrams."})


@pytest.mark.parametrize(
    ("name", "listed_as"),
    [("escitalopram 10 mg", "escitalopram"), ("Lithium", "lithium carbonate ER")],
)
def test_a_change_naming_a_listed_medication_with_its_dose_or_in_part_reaches_its_row(
    name: str, listed_as: str
) -> None:
    (proposal,) = _change(name, CHART)

    assert (proposal.field_key, proposal.item_key) == (MEDICATIONS, listed_as)


def test_a_name_two_listed_medications_begin_with_reaches_neither() -> None:
    chart = _chart(("methylphenidate ER", "36 mg", None), ("methylphenidate IR", "10 mg", None))

    assert _change("methylphenidate", chart) == []
