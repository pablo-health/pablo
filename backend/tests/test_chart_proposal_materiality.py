# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The materiality check on history proposals, both ways.

Every case is a proposal a capable model made in the longitudinal episode
runs, reworded into a synthetic client. A case that must not be offered is one
a clinician would have had to discard at sign; one that must be offered is a
change the chart would be wrong without.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest
from app.chart_proposals.drafting import cited_lines, material, said_this_visit
from app.chart_proposals.materiality import (
    CitedLine,
    OtherFields,
    Verdict,
    admit,
    content_words,
    heard_about,
    lists,
    words,
)
from app.chart_proposals.models import DraftedProposal, Evidence
from app.notes.chart_context import (
    ChartContext,
    ChartHistoryField,
    ChartMedication,
    ChartProblem,
)
from app.notes.chart_fields import Statement, Statements

CLIENT = "client"
DICTATED = "dictated"
INTERVIEW = "interview"


@dataclass(frozen=True)
class Case:
    name: str
    field_key: str
    chart: str
    proposed: str
    cited: tuple[tuple[str, str], ...]
    reason: str | None
    """``None`` when the proposal is offered."""
    said: tuple[str, ...] | None = None
    history: dict[str, str] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    medications: tuple[str, ...] = ()


NOT_OFFERED = [
    Case(
        "present ideation written into past self-harm",
        "past_self_harm",
        "Denies any past suicide attempt or self-harm.",
        "Denies any past suicide attempt or self-harm. Passive suicidal ideation reported "
        "this visit: thoughts that it would be easier not to wake up, most mornings for two "
        "weeks; no intent, no plan.",
        (("Most mornings I think it would be easier not to wake up.", CLIENT),),
        "present-not-past",
    ),
    Case(
        "a diagnosis the problem list has, proposed to an empty prior diagnoses",
        "prior_diagnoses",
        "",
        "Generalized anxiety disorder.",
        (("Generalized anxiety disorder, chronic, stable.", DICTATED),),
        "elsewhere",
        problems=("Generalized anxiety disorder",),
    ),
    Case(
        "prior diagnoses restated where the extraction heard nothing about them",
        "prior_diagnoses",
        "Generalized anxiety disorder diagnosed at 35. Bipolar II disorder diagnosed at 38.",
        "Generalized anxiety disorder diagnosed at 35. Bipolar II disorder diagnosed at 38 at "
        "a prior clinic; changed to rule-out at this clinic pending a mood chart.",
        (("Bipolar II, rule out, pending a four-week mood chart.", DICTATED),),
        "novelty",
        said=(),
    ),
    Case(
        "a therapy intake scheduled for next week",
        "psychotherapy_history",
        "None. Declined therapy at 45; open to it now.",
        "None. Declined therapy at 45; open to it now. Therapy intake appointment scheduled "
        "for the following week.",
        (("I did call the therapist. First appointment is next week.", CLIENT),),
        "transient",
    ),
    Case(
        "weekly therapy begun, inferred from a count of sessions",
        "psychotherapy_history",
        "None. Declined therapy at 45; open to it now.",
        "None. Declined therapy at 45; open to it now. Weekly therapy begun; three sessions "
        "completed as of this visit.",
        (
            ("Three sessions. She has me writing the worries down in the morning.", CLIENT),
            ("How is therapy going?", INTERVIEW),
            ("Anxiety exacerbated by a death in the family; therapy continues.", DICTATED),
        ),
        "transient",
    ),
    Case(
        "a drinking pattern restated with the weekdays",
        "alcohol",
        "One glass of wine two or three evenings a week.",
        "Two glasses of wine on two evenings a week (Tuesday and Friday).",
        (("Tuesday and Friday, a couple of glasses.", CLIENT),),
        "transient",
    ),
    Case(
        "a prescribed benzodiazepine's new dose in the substance field",
        "benzodiazepines",
        "Clonazepam as prescribed, 1 mg twice daily for twelve years; no extra doses.",
        "Clonazepam as prescribed, 0.75 mg twice daily (reduced from 1 mg twice daily as part "
        "of a taper); no extra doses.",
        (("One and a half, one and a half. No extra.", CLIENT),),
        "elsewhere",
        medications=("clonazepam",),
    ),
    Case(
        "a medication another prescriber started, in medical history",
        "medical_history",
        "Obesity; prediabetes, followed by primary care. No surgeries.",
        "Obesity; prediabetes, followed by primary care. No surgeries. Semaglutide 0.25 mg "
        "weekly started by primary care in May.",
        (("My primary care started me on semaglutide in May, the weekly shot.", CLIENT),),
        "elsewhere",
        medications=("semaglutide",),
    ),
    Case(
        "a lab result mentioned in passing",
        "medical_history",
        "Type 2 diabetes and hypertension, both managed by primary care.",
        "Type 2 diabetes and hypertension, both managed by primary care. Most recent primary "
        "care visit April; A1c 7.1, with a target below 7.",
        (("My A1c was seven point one in April.", CLIENT),),
        "elsewhere",
    ),
    Case(
        "extra shifts most weeks",
        "work_school",
        "Veterinary technician at an emergency animal hospital, full time, overnight shifts "
        "three nights a week.",
        "Veterinary technician at an emergency animal hospital, full time, overnight shifts "
        "three nights a week; picking up a fourth overnight most weeks.",
        (("They keep asking me to pick up a fourth overnight, most weeks I do.", CLIENT),),
        "transient",
    ),
    Case(
        "a grandchild expected",
        "relationships",
        "Married 31 years; two adult sons, one local. Describes the marriage as close.",
        "Married 31 years; two adult sons, one local. Describes the marriage as close. Local "
        "son and his wife are expecting a baby in August.",
        (("Our son and his wife are expecting in August.", CLIENT),),
        "transient",
    ),
    Case(
        "a detail only the clinician's question carries",
        "legal_custody",
        "Divorce proceedings pending; no other legal involvement.",
        "Divorce proceedings pending; mediation with a family court mediator in March.",
        (("Did the family court mediator meet with you both in March?", INTERVIEW),),
        "interview",
    ),
    Case(
        "the chart's text reordered",
        "supports",
        "Sister; a church group that meets on Wednesdays; two neighbors.",
        "Two neighbors; a church group that meets on Wednesdays; sister.",
        (("Same people as always, my sister, the church group.", CLIENT),),
        "novelty",
    ),
]

OFFERED = [
    Case(
        "a divorce finalized, from the dictation",
        "relationships",
        "Married 19 years; separated since January 2026, living apart; divorce petition filed "
        "in February 2026. Two adult children, both out of state, in regular contact.",
        "Married 19 years; separated since January 2026, living apart; divorce petition filed "
        "in February 2026; divorce finalized April 2, 2026. Two adult children, both out of "
        "state, in regular contact.",
        (("It's done. The judge signed it.", CLIENT), ("Divorce finalized April 2.", DICTATED)),
        None,
        said=("Divorce finalized April 2, 2026.",),
    ),
    Case(
        "moved in with a girlfriend",
        "living_situation",
        "Lives with his parents in their house since graduating in May 2025.",
        "Lived with his parents from May 2025 until April 1, 2026; now lives with his "
        "girlfriend in a one-bedroom apartment across town.",
        (
            ("Her lease came up and she asked. I said yes.", CLIENT),
            ("Moved in with his girlfriend April 1.", DICTATED),
        ),
        None,
    ),
    Case(
        "started weekly exposure therapy",
        "psychotherapy_history",
        "Supportive therapy for a year at 25; never had exposure and response prevention. "
        "Not currently in therapy.",
        "Supportive therapy for a year at 25. Began weekly exposure and response prevention "
        "with a therapist in March 2026.",
        (("I started ERP with a therapist in March, every week.", CLIENT),),
        None,
    ),
    Case(
        "laid off when the practice closed",
        "work_school",
        "Works full time as a dental hygienist at a group practice, four days a week.",
        "Worked full time as a dental hygienist at a group practice, four days a week, until "
        "the practice closed at the end of March 2026; laid off since April 1.",
        (("The practice closed at the end of March. I've been laid off since.", CLIENT),),
        None,
    ),
    Case(
        "no longer working there, a marker made partly of a stop word",
        "work_school",
        "Works full time at the library.",
        "Worked at the library until March; no longer working there.",
        (("I worked at the library until March. I'm no longer working there.", CLIENT),),
        None,
        said=("Worked at the library until March; no longer working there.",),
    ),
    Case(
        "moved in with a partner, said as moved in",
        "living_situation",
        "Lives alone in a studio.",
        "Lived alone in a studio until June; moved in with partner in June 2026.",
        (("We moved in together in June.", CLIENT),),
        None,
    ),
    Case(
        "started vaping",
        "tobacco_nicotine",
        "Denies tobacco or nicotine use.",
        "Denied tobacco or nicotine use through April 2026. Began vaping nicotine in early "
        "May 2026: about one low-nicotine pod every four days, most days.",
        (("I started vaping. A pod lasts me about four days.", CLIENT),),
        None,
    ),
    Case(
        "joined a peer support group",
        "supports",
        "Husband; her mother; one close friend.",
        "Husband; her mother; one close friend; an OCD peer support group joined in May "
        "2026, meeting every other week.",
        (
            ("I went. Twice now. Every other Tuesday, in the church basement.", CLIENT),
            ("Joined an OCD peer support group in May.", DICTATED),
        ),
        None,
    ),
    Case(
        "a father's stroke in the family's medical history",
        "family_medical",
        "Father with hypertension; mother died of breast cancer at 55.",
        "Father with hypertension; stroke in April 2026. Mother died of breast cancer at 55.",
        (("My dad had a stroke, April twentieth.", CLIENT),),
        None,
    ),
    Case(
        "a substance screen's denial into an empty field, question and answer cited",
        "cocaine",
        "",
        "Denies.",
        (("Any cocaine?", INTERVIEW), ("No, never.", CLIENT)),
        None,
    ),
    Case(
        "an imported document's baseline into an empty field",
        "alcohol",
        "",
        "A beer or two on weekends.",
        (("Alcohol: a beer or two on weekends.", "unknown"),),
        None,
    ),
]


def _verdict(case: Case) -> Verdict:
    proposal = DraftedProposal(
        field_key=case.field_key,
        proposed_text=case.proposed,
        what_changed="",
        evidence=(),
    )
    cited = [CitedLine(text, speaker) for text, speaker in case.cited]  # type: ignore[arg-type]
    others = OtherFields(history=case.history, problems=case.problems, medications=case.medications)
    return admit(proposal, case.chart, case.said, cited, others)


@pytest.mark.parametrize("case", NOT_OFFERED, ids=lambda c: c.name)
def test_a_restatement_a_passing_event_or_a_misplaced_fact_is_not_offered(case: Case) -> None:
    assert _verdict(case) == Verdict(admitted=False, reason=case.reason)  # type: ignore[arg-type]


@pytest.mark.parametrize("case", OFFERED, ids=lambda c: c.name)
def test_a_lasting_change_the_client_or_the_dictation_states_is_offered(case: Case) -> None:
    assert _verdict(case).admitted


# --- Normalising --------------------------------------------------------------------------


def test_numbers_and_dates_compare_as_digits_and_iso() -> None:
    assert words("three nights") == words("3 nights")
    assert "2026-04-02" in words("finalized April 2, 2026")
    assert "2026-04" in words("closed in April 2026")
    assert content_words("Reports the sister's house") == {"sister", "house"}


def test_the_committed_lists_load_and_every_marker_kind_is_non_empty() -> None:
    vocab = lists()
    assert vocab.state
    assert vocab.transient
    assert vocab.entities
    assert vocab.stop
    assert all(vocab.state_groups.values())
    assert ("laid", "off") in vocab.state


# --- Who said a line --------------------------------------------------------------------


def test_the_clinician_before_the_clients_last_line_is_the_interview() -> None:
    lines = cited_lines(
        {
            0: "[00:01] Therapist: How have things been?",
            1: "[00:05] Client: The divorce was finalized on April 2.",
            2: "[00:20] Therapist: Note. Divorce finalized April 2.",
        }
    )
    assert [lines[n].speaker for n in range(3)] == [INTERVIEW, CLIENT, DICTATED]
    assert lines[1].text == "The divorce was finalized on April 2."


def test_a_transcript_with_no_client_line_cannot_tell_who_asked() -> None:
    lines = cited_lines({0: "[00:01] Therapist: Dictation. Divorce finalized April 2."})
    assert lines[0].speaker == "unknown"


# --- In the proposal step ---------------------------------------------------------------

SEGMENTS = {
    0: "[00:01] Therapist: Anything new since last time?",
    1: "[00:05] Client: Primary care started me on semaglutide in May.",
    2: "[00:12] Client: And the divorce was finalized on April 2.",
}


def _drafted(field_key: str, text: str, ids: tuple[int, ...]) -> DraftedProposal:
    return DraftedProposal(
        field_key=field_key,
        proposed_text=text,
        what_changed="",
        evidence=tuple(Evidence(i, SEGMENTS[i]) for i in ids),
    )


def _chart() -> ChartContext:
    return ChartContext(
        allergy_status="nkda",
        problems=(ChartProblem("Generalized anxiety disorder", "F41.1", "active"),),
        medications=(ChartMedication("sertraline", "50 mg", "every morning", "psychiatric"),),
        history=(
            ChartHistoryField("relationships", "Married; separated in January.", date(2026, 1, 9)),
            ChartHistoryField("medical_history", "Prediabetes.", date(2026, 1, 9)),
        ),
    )


def test_the_step_offers_the_divorce_and_keeps_the_misplaced_medication_with_its_reason() -> None:
    divorce = _drafted(
        "relationships", "Married; separated in January; divorce finalized April 2, 2026.", (2,)
    )
    semaglutide = _drafted(
        "medical_history", "Prediabetes. Semaglutide 0.25 mg weekly started in May.", (1,)
    )
    statements = Statements(
        fields=(
            Statement("relationships", stated="divorce finalized", segment_ids=(2,)),
            Statement("medical_history", stated="semaglutide started", segment_ids=(1,)),
        )
    )
    said = said_this_visit({"relationships", "medical_history"}, statements, None)
    offered, considered = material([divorce, semaglutide], _chart(), SEGMENTS, said)
    assert offered == [divorce]
    assert [(c.field_key, c.reason, c.evidence_segment_ids) for c in considered] == [
        ("medical_history", "elsewhere", (1,))
    ]


def test_a_field_the_extraction_covers_and_heard_nothing_about_is_not_offered() -> None:
    divorce = _drafted(
        "relationships", "Married; separated in January; divorce finalized April 2, 2026.", (2,)
    )
    said = said_this_visit({"relationships"}, Statements(), None)
    offered, considered = material([divorce], _chart(), SEGMENTS, said)
    assert offered == []
    assert [c.reason for c in considered] == ["novelty"]


def test_without_statements_the_drafts_marks_say_what_was_said() -> None:
    divorce = _drafted(
        "relationships", "Married; separated in January; divorce finalized April 2, 2026.", (2,)
    )
    draft = {
        "social_history": {
            "relationships": 'Married; separated in January. (stated this visit: "finalized")'
        }
    }
    offered, _ = material(
        [divorce], _chart(), SEGMENTS, said_this_visit({"relationships"}, None, draft)
    )
    assert offered == [divorce]


def test_a_field_the_note_type_drafts_itself_is_not_asked_for_an_extraction() -> None:
    divorce = _drafted(
        "relationships", "Married; separated in January; divorce finalized April 2, 2026.", (2,)
    )
    said = said_this_visit((), Statements(), None)
    offered, considered = material([divorce], _chart(), SEGMENTS, said)
    assert (offered, considered) == ([divorce], ())


def test_a_fact_the_extraction_filed_under_a_neighbouring_field_was_still_heard() -> None:
    said = {
        "family_medical": [],
        "family_psychiatric": ["my dad had a stroke, April twentieth. Left side. He can't drive."],
        "work_school": [],
    }
    stroke = heard_about(
        "family_medical",
        "Father with hypertension; stroke in April 2026, left side; cannot drive.",
        "Father with hypertension.",
        said,
    )
    restated = heard_about(
        "work_school", "Teacher, full time, at the same school.", "Teacher, full time.", said
    )
    assert stroke == said["family_psychiatric"]
    assert restated == []
    assert heard_about("supports", "Sister.", "", said) is None


def test_medications_and_allergies_pass_through_untouched() -> None:
    allergy = DraftedProposal(
        field_key="allergies",
        item_key="penicillin",
        proposed_text="hives",
        what_changed="",
        evidence=(Evidence(0, SEGMENTS[0]),),
    )
    offered, considered = material([allergy], _chart(), SEGMENTS, {"allergies": []})
    assert (offered, considered) == ([allergy], ())
