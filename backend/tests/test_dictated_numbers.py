# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Codes, times and diagnosis codes the clinician dictated, read back as dictated.

The heard strings are what a speech recognizer wrote for synthesized
dictation of the eval visits (``evals/note_templates/visits.py``): each test
pairs one with what the clinician said.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.models import Patient, Transcript
from app.notes import NoteFieldDef, NoteSectionDef, NoteTypeDefinition
from app.notes.chart_context import ChartContext, ChartProblem
from app.notes.client_present import DICTATED_HEADING
from app.notes.dictated_numbers import (
    CODE_NOT_STATED,
    PROCEDURE_CODES,
    known_diagnosis_codes,
    normalise_dictated_numbers,
    without_unparsed_codes,
)
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import StructuredCompletion, StructuredLLMGateway
from evals.note_templates import visits

VISIT = (
    "[00:00:04] Therapist: Hi Casey. Are you at home today?\n"
    "[00:00:09] Client: Yes, at home. My sister called at 1014 to 10:55 last night.\n"
)


def _tail(dictated: str, known: tuple[str, ...] = ()) -> str:
    """The dictated line after the client's last turn, as normalised."""
    content = normalise_dictated_numbers(f"{VISIT}[00:40:31] Therapist: {dictated}", known).content
    return content.rsplit("Therapist: ", 1)[1]


# ---------------------------------------------------------------------------
# Billing codes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "said"),
    [
        # Every visit that dictated an E/M code with an add-on.
        ("Billing 992-149-0836.", "Billing 99214 plus 90836."),
        ("Billing 992-149-0833.", "Billing 99214 plus 90833."),
        ("Billing 992 149 0836.", "Billing 99214 plus 90836."),
        ("Billing 9921490838.", "Billing 99214 plus 90838."),
        ("Billing 992-05-90792.", "Billing 99205 and 90792."),
        ("Billing 992-14.", "Billing 99214."),
        ("Billing 908 36.", "Billing 90836."),
    ],
)
def test_a_run_together_code_reads_as_the_codes_said(heard: str, said: str) -> None:
    assert _tail(heard) == said


@pytest.mark.parametrize(
    "heard",
    [
        "Billing 90792.",  # a single code said alone was heard
        "Billing 99214.",
        "Billing 99214 plus 90836.",
        "Billing 99214, 90836.",
        "Call 555-123-4567 or 988 if anything gets worse.",
        "Billing 123-456-7890.",  # ten digits, but neither half a code
        "Return in four weeks, number 30, one refill.",
    ],
)
def test_digits_that_are_not_a_run_together_code_stay_as_heard(heard: str) -> None:
    assert _tail(heard) == heard


def test_the_known_codes_are_the_billed_ones() -> None:
    assert {"99202", "99205", "99212", "99215", "90833", "90836", "90838"} <= PROCEDURE_CODES
    assert {"90791", "90792"} <= PROCEDURE_CODES
    assert "99211" not in PROCEDURE_CODES


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "said"),
    [
        # "10:14 to 10:55, 41 minutes", both visits that dictated it.
        (
            "Psychotherapy from 1014 to 105541 minutes. Billing 992-149-0836.",
            "Psychotherapy from 10:14 to 10:55, 41 minutes. Billing 99214 plus 90836.",
        ),
        (
            "Psychotherapy from 10:14 to 10:5541 minutes.",
            "Psychotherapy from 10:14 to 10:55, 41 minutes.",
        ),
        # "3:02 to 3:41".
        ("Psychotherapy from 3:02 to 3. 41.", "Psychotherapy from 3:02 to 3:41."),
        ("Psychotherapy from 3:02 to 3 41.", "Psychotherapy from 3:02 to 3:41."),
        ("Psychotherapy from 302 till 3:41.", "Psychotherapy from 3:02 till 3:41."),
        ("Psychotherapy from 10:14 until 1055.", "Psychotherapy from 10:14 until 10:55."),
        ("Ended at 10:5541 minutes.", "Ended at 10:55, 41 minutes."),
        # Minutes shorter than the window: therapy within a longer visit.
        ("Seen from 9:02 to 9:5838 minutes.", "Seen from 9:02 to 9:58, 38 minutes."),
    ],
)
def test_a_run_together_window_reads_as_the_time_said(heard: str, said: str) -> None:
    assert _tail(heard) == said


@pytest.mark.parametrize(
    "heard",
    [
        "Visit from 9:02 to 9:58 by video.",
        "Visit from 2:00 o'clock to 2:55 by video.",
        "Psychotherapy 30 minutes. Psychoeducation on sleep hygiene, 18 minutes.",
        # A dose change is not a window: neither side reads as a clock time.
        "Increase sertraline from 150 to 200 milligrams.",
        # More minutes than the window holds: no reading fits, so none is made.
        "Psychotherapy from 10:14 to 10:5560 minutes.",
        # Scale scores, not times.
        "Gad 7:14 rule out ADHD. phq917. GAD-7 14.",
        "Continue Adderall XR20PDMP check today.",
    ],
)
def test_numbers_that_are_not_a_garbled_time_stay_as_heard(heard: str) -> None:
    assert _tail(heard) == heard


# ---------------------------------------------------------------------------
# Diagnosis codes
# ---------------------------------------------------------------------------

HEARD_DIAGNOSES = (
    "major depressive disorder single episode moderate F32 17 months of depressed mood, "
    "anhedonia, insomnia phq917 generalized anxiety disorder F41 1 years of excessive worry "
    "Gad 7:14 rule out ADHD predominantly inattentive F90 0 childhood inattention by report"
)


def test_a_code_on_the_chart_gets_its_decimal_back() -> None:
    assert _tail(HEARD_DIAGNOSES, ("F32.1", "F41.1", "F90.0")) == (
        "major depressive disorder single episode moderate F32.1, 7 months of depressed mood, "
        "anhedonia, insomnia phq917 generalized anxiety disorder F41.1 years of excessive worry "
        "Gad 7:14 rule out ADHD predominantly inattentive F90.0 childhood inattention by report"
    )


def test_a_code_nowhere_on_the_chart_stays_as_heard_and_unparsed() -> None:
    numbers = normalise_dictated_numbers(f"{VISIT}[00:40:31] Therapist: {HEARD_DIAGNOSES}")

    assert numbers.content.endswith(HEARD_DIAGNOSES)
    assert numbers.unparsed_stems == {"F32", "F41", "F90"}


def test_a_decimal_is_never_invented() -> None:
    # F32.0 is on the chart, but "F32 17" cannot be read as it.
    numbers = normalise_dictated_numbers(
        f"{VISIT}[00:40:31] Therapist: Depression F32 17 months.", ("F32.0",)
    )

    assert numbers.content.endswith("Depression F32 17 months.")
    assert numbers.unparsed_stems == {"F32"}


def test_a_code_said_plainly_is_stated_and_left_alone() -> None:
    dictated = "Panic disorder, F41.0. Generalized Anxiety Disorder, F41.1 Gad7.16."
    numbers = normalise_dictated_numbers(f"{VISIT}[00:40:31] Therapist: {dictated}")

    assert numbers.content.endswith(dictated)
    assert numbers.stated_codes == {"F41.0", "F41.1"}
    assert not numbers.unparsed_stems


def test_an_unparsed_code_is_not_stated_in_the_note() -> None:
    numbers = normalise_dictated_numbers(
        f"{VISIT}[00:40:31] Therapist: Panic disorder, F41.0. Generalized anxiety F41 1 years.",
        ("F32.1",),
    )
    content: dict[str, Any] = {
        "assessment": {
            "diagnoses": [
                {"label": "Major depressive disorder", "code": "F32.1", "status": None},
                {"label": "Panic disorder", "code": "F41.0", "status": None},
                {"label": "Generalized anxiety disorder", "code": "F41.11", "status": None},
                {"label": "Insomnia", "code": None, "status": None},
            ]
        }
    }

    codes = [d["code"] for d in without_unparsed_codes(content, numbers)["assessment"]["diagnoses"]]

    assert codes == ["F32.1", "F41.0", CODE_NOT_STATED, None]


def test_the_charts_own_code_is_never_unset() -> None:
    # F32.0 is listed; the dictated "F32 17" cannot be read as it, so it stays unparsed.
    numbers = normalise_dictated_numbers(
        f"{VISIT}[00:40:31] Therapist: Depression F32 17 months.", ("F32.0",)
    )
    content: dict[str, Any] = {
        "assessment": {
            "diagnoses": [
                {"label": "Major depressive disorder, mild", "code": "F32.0", "status": None},
                {"label": "Depression", "code": "F32.17", "status": None},
            ]
        }
    }

    codes = [d["code"] for d in without_unparsed_codes(content, numbers)["assessment"]["diagnoses"]]

    assert codes == ["F32.0", CODE_NOT_STATED]


def test_a_code_said_plainly_after_the_recording_is_stated() -> None:
    content = f"{VISIT}\n\n{DICTATED_HEADING}\nDepression F32 1. Anxiety F41.1."

    numbers = normalise_dictated_numbers(content)

    assert numbers.unparsed_stems == {"F32"}
    assert numbers.stated_codes == {"F41.1"}


def test_the_known_codes_are_the_charts_and_the_notes() -> None:
    chart = ChartContext(problems=(ChartProblem("Major depressive disorder", "F32.1", "active"),))
    note = {"assessment": {"diagnoses": [{"label": "GAD", "code": "F41.1", "status": None}]}}

    assert known_diagnosis_codes(chart, note) == {"F32.1", "F41.1"}
    assert known_diagnosis_codes(None, None) == set()


# ---------------------------------------------------------------------------
# Only the clinician's dictation
# ---------------------------------------------------------------------------


def test_the_clients_words_are_never_rewritten() -> None:
    content = (
        "[00:00:04] Therapist: Any changes?\n"
        "[00:00:09] Client: My new number is 992-149-0836, and F32 17 is on my old form.\n"
        "[00:00:20] Therapist: Thanks. Billing 992-149-0836.\n"
        "[00:00:25] Client: Okay.\n"
        "[00:00:27] Therapist: Psychotherapy from 1014 to 105541 minutes.\n"
    )

    normalised = normalise_dictated_numbers(content, ("F32.1",)).content.splitlines()

    assert normalised[1] == content.splitlines()[1]
    assert normalised[2] == "[00:00:20] Therapist: Thanks. Billing 99214 plus 90836."
    # A short client turn after the dictation began neither ends it nor is rewritten.
    assert normalised[3] == "[00:00:25] Client: Okay."
    assert normalised[4] == "[00:00:27] Therapist: Psychotherapy from 10:14 to 10:55, 41 minutes."


def test_a_transcript_with_the_timestamp_on_its_own_line() -> None:
    content = (
        "[00:00:08]\nClient: Yes, at home today.\n"
        "[00:39:10]\nTherapist: Note for the record. Billing 992-149-0833."
    )

    assert normalise_dictated_numbers(content).content.endswith("Billing 99214 plus 90833.")


def test_without_a_client_turn_only_the_clinicians_channel_is_read() -> None:
    content = (
        "[00:00:04] Speaker B: Call me at 992-149-0836.\n"
        "[00:00:09] Therapist: Billing 992-149-0836.\n"
    )

    assert normalise_dictated_numbers(content).content.splitlines() == [
        "[00:00:04] Speaker B: Call me at 992-149-0836.",
        "[00:00:09] Therapist: Billing 99214 plus 90836.",
    ]


def test_a_later_dictation_is_read_too() -> None:
    dictated = "Psychotherapy from 3:02 to 3. 41. Billing 992-149-0836."
    content = f"{VISIT}\n\n{DICTATED_HEADING}\n{dictated}"

    assert normalise_dictated_numbers(content).content.endswith(
        f"{DICTATED_HEADING}\nPsychotherapy from 3:02 to 3:41. Billing 99214 plus 90836."
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

NOW = datetime(2026, 10, 9, tzinfo=UTC)
DEFINITION = NoteTypeDefinition(
    key="custom.dictated",
    label="Follow-up",
    description="",
    tier="core",
    context="session",
    system_prompt="Draft it.",
    sections=(
        NoteSectionDef(
            key="assessment",
            label="Assessment",
            fields=(
                NoteFieldDef(key="diagnoses", label="Diagnoses", kind="diagnoses"),
                NoteFieldDef(key="visit_details", label="Visit details", kind="text"),
            ),
        ),
    ),
)


class _Gateway(StructuredLLMGateway):
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.prompts: list[str] = []

    def complete_structured(self, **kwargs: Any) -> StructuredCompletion:
        self.prompts.append(kwargs["user_prompt"])
        return StructuredCompletion(data=self.data)


def _generate(gateway: _Gateway, chart: ChartContext | None = None) -> dict[str, Any]:
    patient = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
    transcript = (
        f"{VISIT}[00:40:31] Therapist: Major depressive disorder, F32 17 months of low mood. "
        "Psychotherapy from 1014 to 105541 minutes. Billing 992-149-0836."
    )
    return (
        RegistryNoteGenerationService(llm_gateway=gateway)
        .generate_note(
            DEFINITION.key,
            Transcript(format="txt", content=transcript),
            patient,
            NOW,
            definition=DEFINITION,
            chart=chart,
        )
        .content
    )


def test_the_draft_reads_the_codes_and_times_as_dictated() -> None:
    gateway = _Gateway({"assessment": {"diagnoses": [], "visit_details": ""}})
    chart = ChartContext(problems=(ChartProblem("Major depressive disorder", "F32.1", "active"),))

    _generate(gateway, chart)

    prompt = gateway.prompts[0]
    assert "F32.1, 7 months of low mood" in prompt
    assert "from 10:14 to 10:55, 41 minutes" in prompt
    assert "Billing 99214 plus 90836." in prompt
    # The client's words reach the draft as said.
    assert "My sister called at 1014 to 10:55 last night." in prompt


def test_a_drafted_code_read_from_a_garbled_one_is_not_stated() -> None:
    reply = {"diagnoses": [{"label": "Major depressive disorder", "code": "F32.17"}]}
    gateway = _Gateway({"assessment": {**reply, "visit_details": "99214, 90836"}})

    content = _generate(gateway)

    assert content["assessment"]["diagnoses"] == [
        {"label": "Major depressive disorder", "code": CODE_NOT_STATED, "status": None}
    ]


def test_the_eval_visit_as_heard_reads_back_as_dictated() -> None:
    heard = visits.SUPPORTIVE_ONLY_AS_HEARD
    assert "Billing 992-149-0836." in heard
    assert "from 302 to 34139 minutes" in heard
    assert "Depression F32 0 stable" in heard

    content = normalise_dictated_numbers(heard, ("F32.0",)).content

    assert "Depression F32.0 stable" in content
    assert "Psychotherapy from 3:02 to 3:41, 39 minutes. Billing 99214 plus 90836." in content
