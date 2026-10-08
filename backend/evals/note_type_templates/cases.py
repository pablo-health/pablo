# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the note-type template eval.

Each case drafts one of a template's own sample visits (the synthetic
transcripts Settings offers under "Try it") and says what a faithful draft of
that visit looks like. The transcripts live in the template files; nothing
here is about a real person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.notes.spec_templates import TEMPLATES_DIR

TEMPLATES = TEMPLATES_DIR

NOT_COVERED = ("not stated", "not asked")
"""What a field the visit did not cover must say (compared without case or full stop)."""


@dataclass(frozen=True)
class TemplateCase:
    """One template sample, drafted, and what the draft must and must not hold.

    Fields are ``section.field``. Every field not named here must be filled.
    ``not_covered`` fields must say "Not stated." or "Not asked."; ``empty``
    fields must be blank; ``may_be_empty`` fields may be either. Diagnoses
    must come back with exactly ``stated_codes``, each item carrying its code,
    and ``rule_out`` codes marked as such. No value may contain a
    ``forbidden`` string (a code or level the clinician never said).

    A case may bring its own ``transcript`` instead of a template sample.
    ``recorded_call`` drafts it as a two-channel call, so the clinician's
    turns after the client's last line reach the model as the dictated
    addendum. Each ``quoted`` pair is a field and a phrase it must quote.
    With ``fill_unnamed`` off, fields the case does not name are not graded.
    ``therapy_starts_between`` is ``(low, high)`` in seconds into the
    recording: every psychotherapy start the draft proposes is at or after
    ``low``, and the first one offered is no later than ``high``.
    ``confirmed_start_seconds`` is the therapy start the clinician confirms:
    once confirmed, the psychotherapy time field must read the window from
    there to where the client left, as the visit-times panel writes it.

    A case with a ``redraft_dictation`` is then drafted again with that
    line dictated afterwards, from the first draft, as "Dictate more" does.
    Each ``kept_facts`` entry (alternative spellings of one fact) must be in
    the first draft and still in the redraft; each ``added_facts`` entry
    must be in the redraft. The redraft is graded like the first draft too.
    """

    name: str
    template: str
    sample: str
    inputs: dict[str, str]
    not_covered: tuple[str, ...] = ()
    empty: tuple[str, ...] = ()
    may_be_empty: tuple[str, ...] = ()
    diagnoses_field: str | None = None
    stated_codes: frozenset[str] = frozenset()
    rule_out: frozenset[str] = frozenset()
    forbidden: tuple[str, ...] = field(default_factory=tuple)
    transcript: str | None = None
    recorded_call: bool = False
    quoted: tuple[tuple[str, str], ...] = ()
    fill_unnamed: bool = True
    therapy_starts_between: tuple[float, float] | None = None
    confirmed_start_seconds: float | None = None
    redraft_dictation: str | None = None
    kept_facts: tuple[tuple[str, ...], ...] = ()
    added_facts: tuple[tuple[str, ...], ...] = ()


def load_template(template: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((TEMPLATES / f"{template}.json").read_text())
    return loaded


def sample_transcript(template: str, sample: str) -> str:
    samples = load_template(template)["samples"]
    return str(next(s["transcript"] for s in samples if s["id"] == sample))


_PSYCHOTHERAPY = tuple(
    f"psychotherapy.{key}"
    for key in ("psychotherapy_time", "modality_interventions", "response", "goal_plan")
)

INTAKE_NEW_CLIENT = TemplateCase(
    name="psychiatric-evaluation-new-client",
    template="psychiatric_evaluation",
    sample="new_client",
    inputs={
        "place_of_service": "Telehealth",
        "client_location": "Home",
        "provider_location": "Office",
        "visit_code": "Psychiatric diagnostic evaluation (90792)",
    },
    not_covered=(
        "psychiatric_history.legal_custody",
        "psychiatric_ros.eating",
        "psychiatric_ros.trauma_responses",
    ),
    empty=_PSYCHOTHERAPY,
    may_be_empty=("treatment_plan.off_label", "prescriptions.pdmp"),
    diagnoses_field="assessment.diagnoses",
    stated_codes=frozenset({"F32.1", "F41.1", "F90.0"}),
    rule_out=frozenset({"F90.0"}),
    # An E/M level, a psychotherapy add-on, or an alcohol use disorder code:
    # none was stated, so any of them would be invented.
    forbidden=("99202", "99203", "99204", "99205", "90833", "90836", "90838", "F10."),
)


# A medication check by video. The client never speaks of risk; the clinician
# states it, and the mental status findings, in the addendum dictated after the
# client has gone. Neither the visit nor the addendum covers self-harm,
# orientation or cognition.
_FOLLOW_UP_WITH_ADDENDUM = "\n".join(
    [
        "[00:00:04] Therapist: Hi, good to see you again. Are you at home today?",
        "[00:00:08] Client: Yes, I'm at home.",
        "[00:00:12] Therapist: How has the sertraline been since we went up to 100?",
        "[00:00:20] Client: Better. I'm sleeping through most nights and the mornings "
        "are less heavy. A little nausea the first week, gone now.",
        "[00:00:41] Therapist: Good. Any missed doses?",
        "[00:00:44] Client: Maybe one, when I travelled.",
        "[00:00:50] Therapist: That's fine. We'll stay at 100 and meet again in six weeks.",
        "[00:00:58] Client: Sounds good. Thank you, see you then.",
        "[00:01:20] Therapist: Addendum for the note. Client denies suicidal ideation, "
        "intent or plan. Overall risk is low.",
        "[00:01:34] Therapist: Mood described as better, affect brighter than last "
        "visit. Continue sertraline 100 milligrams daily.",
    ]
)

FOLLOW_UP_ADDENDUM = TemplateCase(
    name="psychiatric-follow-up-dictated-addendum",
    template="psychiatric_follow_up",
    sample="",
    transcript=_FOLLOW_UP_WITH_ADDENDUM,
    recorded_call=True,
    inputs={"place_of_service": "Telehealth", "client_location": "Home"},
    quoted=(
        ("risk.suicidal_homicidal_ideation", "denies suicidal ideation"),
        ("risk.overall_risk", "risk is low"),
        ("mse.mood_affect", "brighter"),
    ),
    not_covered=("risk.self_harm_violence", "mse.orientation", "mse.cognition"),
    fill_unnamed=False,
)


# A 65-minute visit: a 12-minute medication check, then a long therapy block
# the client asked for. The therapy start must come after the medication
# check; the client-present span from 0:00 would overstate it by 12 minutes.
_MED_CHECK_THEN_THERAPY = "\n".join(
    [
        "[00:00:04] Therapist: Hi, good to see you. Are you at home today?",
        "[00:00:08] Client: Yes, at home.",
        "[00:00:15] Therapist: How has the bupropion been at 300?",
        "[00:00:24] Client: Energy is better. I'm getting up on time most days.",
        "[00:02:10] Therapist: Any trouble sleeping, headaches, anything new?",
        "[00:02:18] Client: A little dry mouth, that's it.",
        "[00:05:30] Therapist: Blood pressure at the pharmacy was 124 over 80, which is fine.",
        "[00:08:40] Client: Good. I was worried about that.",
        "[00:10:55] Therapist: We'll stay at 300 milligrams, and I'll see you in four weeks "
        "for medication.",
        "[00:11:40] Client: Okay. Can we use the rest of the time to talk about my brother? "
        "It's been really hard.",
        "[00:12:05] Therapist: Of course. Tell me what happened this week with him.",
        "[00:12:20] Client: He moved back in, and every evening turns into an argument "
        "about money.",
        "[00:18:45] Therapist: When the argument starts, what goes through your mind?",
        "[00:19:02] Client: That he's taking advantage of me, and that I can't say no.",
        "[00:27:30] Therapist: Let's look at the evidence for 'I can't say no'. When did you "
        "last say no to him?",
        "[00:28:10] Client: Last month, about the car. It went okay, actually.",
        "[00:39:50] Therapist: Let's practice how you'd set the limit about rent, in your words.",
        "[00:40:20] Client: I'd say I need you to pay half by the first, or find another place.",
        "[00:52:15] Therapist: How did that feel to say out loud?",
        "[00:52:30] Client: Scary, but clearer. I think I can do it this weekend.",
        "[01:03:40] Therapist: For next time, try the conversation and write down what he said "
        "and what you felt.",
        "[01:04:45] Client: I will. Thank you, this helped a lot. See you next month.",
        "[01:05:30] Therapist: Addendum for the note. Client denies suicidal ideation. "
        "Mood improved, affect brighter.",
        "[01:05:44] Therapist: Psychotherapy was about 52 minutes.",
    ]
)

FOLLOW_UP_THERAPY_START = TemplateCase(
    name="psychiatric-follow-up-therapy-start",
    template="psychiatric_follow_up",
    sample="",
    transcript=_MED_CHECK_THEN_THERAPY,
    recorded_call=True,
    inputs={"place_of_service": "Telehealth", "client_location": "Home"},
    # From the client asking to talk (11:40) to the first therapy question (12:20).
    therapy_starts_between=(11 * 60 + 40, 12 * 60 + 20),
    # The clinician confirms the turn where they took up the client's request
    # (12:05). They dictated the minutes but no clock times, so the window
    # supplies the times and agrees with the minutes.
    confirmed_start_seconds=12 * 60 + 5,
    fill_unnamed=False,
)


# A medication check with a home blood pressure and a refill, then therapy.
# The clinician's plan restates both; afterwards they dictate one more line
# for the plan, about the same blood pressure.
_MED_CHECK_THEN_REDRAFT = "\n".join(
    [
        "[00:00:04] Therapist: Hi, good to see you. Are you at home today?",
        "[00:00:08] Client: Yes, at home.",
        "[00:00:15] Therapist: How has the methylphenidate been at 36 milligrams?",
        "[00:00:24] Client: Good. It wears off around four, but mornings are much better.",
        "[00:02:10] Therapist: Did you check your blood pressure at home like we talked about?",
        "[00:02:18] Client: I did. It was 124 over 78, and about the same the other times.",
        "[00:03:30] Therapist: That's right where I'd want it. I'll send the next 30 day supply "
        "to your usual pharmacy.",
        "[00:04:05] Client: Great. Can we spend the rest of the time on work? My new manager "
        "has been hard.",
        "[00:04:20] Therapist: Of course, let's get into that. What happened this week?",
        "[00:05:00] Client: Every document I hand in comes back covered in comments, and I freeze.",
        "[00:12:30] Therapist: When the comments come in, what goes through your mind?",
        "[00:12:45] Client: That I'm not good enough, and that they'll find out.",
        "[00:18:10] Therapist: Let's try replying within two hours, and write down what you "
        "predict will happen first.",
        "[00:21:40] Client: Okay, I'll try that this week. Thank you, see you in four weeks.",
        "[00:22:30] Therapist: Addendum for the note. Client denies suicidal ideation. Mood "
        "stressed, affect anxious but reactive.",
        "[00:22:50] Therapist: Plan. Continue methylphenidate 36 milligrams every morning. "
        "30 day supply sent to the usual pharmacy. Home blood pressure 124 over 78. Recheck "
        "at the next visit.",
        "[00:23:10] Therapist: Psychotherapy was about 17 minutes. Cognitive behavioral "
        "therapy for work-related anxiety.",
    ]
)

FOLLOW_UP_REDRAFT = TemplateCase(
    name="psychiatric-follow-up-redraft-keeps-facts",
    template="psychiatric_follow_up",
    sample="",
    transcript=_MED_CHECK_THEN_REDRAFT,
    recorded_call=True,
    inputs={"place_of_service": "Telehealth", "client_location": "Home"},
    # From "let's get into that" (4:20) to the client leaving: 17 minutes.
    confirmed_start_seconds=4 * 60 + 20,
    # As speech recognition transcribed it, sentence break and all.
    redraft_dictation=(
        "Add to the plan. The client will send me a home. Blood pressure reading in two weeks."
    ),
    kept_facts=(("124/78", "124 over 78"), ("30 day", "30-day"), ("pharmacy",)),
    added_facts=(("two weeks",),),
    fill_unnamed=False,
)


def all_cases() -> tuple[TemplateCase, ...]:
    return (INTAKE_NEW_CLIENT, FOLLOW_UP_ADDENDUM, FOLLOW_UP_THERAPY_START, FOLLOW_UP_REDRAFT)
