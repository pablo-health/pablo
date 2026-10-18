# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Synthetic plan text for the homework read, and what it should find.

**Every case is written from scratch.** No real note, transcript or person
is behind any of them (see ``backend/evals/README.md``). They are shaped like
the prose fields the read is given: a psychiatric follow-up's therapy goal
and plan, therapy cadence and lifestyle counselling; an initial evaluation's
plan items and between-visit instructions.

Each case says which tasks must be found (``expected``: every listed word
must appear in one kept task, case-insensitive) and what must never become
homework (``forbidden``: a kept task containing any of these words is a
false positive — a medication, a lab, an appointment, a crisis step).

Three groups: plans with homework among other things, plans with none (the
right answer is an empty list), and plans whose wording invites a mistake
(a medication schedule that reads like a task, an education topic with no
task, a task for the clinician rather than the client).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    text: str


@dataclass(frozen=True)
class EvalCase:
    name: str
    group: str
    fields: tuple[Field, ...]
    #: One entry per task that must be found: the words its text must contain.
    expected: tuple[tuple[str, ...], ...] = ()
    #: Words no kept task may contain.
    forbidden: tuple[str, ...] = field(default=())


_MEDS = ("mg", "sertraline", "bupropion", "lithium", "dose", "taper", "lab", "tsh", "cbc")
_VISITS = ("follow-up", "follow up", "return", "weeks", "appointment", "referral")
_CRISIS = ("988", "emergency", "crisis line", "911")


def _goal(text: str) -> Field:
    return Field("psychotherapy.goal_plan", "Goal and plan", text)


def _cadence(text: str) -> Field:
    return Field("psychotherapy.therapy_cadence", "Therapy plan and cadence", text)


def _lifestyle(text: str) -> Field:
    return Field("plan.lifestyle_counseling", "Lifestyle counseling", text)


def _plan_items(text: str) -> Field:
    return Field("treatment_plan.plan_items", "Plan", text)


def _between(text: str) -> Field:
    return Field("follow_up.between_visits", "Between visits and emergencies", text)


CASES: tuple[EvalCase, ...] = (
    # --- homework among other things ------------------------------------
    EvalCase(
        "panic-log-and-breathing",
        "mixed",
        (
            _goal(
                "Goal: fewer panic attacks at work. Continue CBT for panic. Client agreed to "
                "log each panic episode in the app and to practise diaphragmatic breathing "
                "twice daily."
            ),
            _lifestyle("Reduced caffeine discussed; client will stop coffee after noon."),
        ),
        expected=(("log", "panic"), ("breathing",), ("coffee",)),
        forbidden=_MEDS + _VISITS,
    ),
    EvalCase(
        "sleep-diary-with-med-change",
        "mixed",
        (
            _goal("Insomnia improving. Continue CBT-I; client to keep a sleep diary nightly."),
            _lifestyle(
                "Sleep hygiene reviewed. Increase trazodone to 100 mg at bedtime. "
                "Client will get out of bed if not asleep within 20 minutes."
            ),
        ),
        expected=(("sleep diary",), ("out of bed",)),
        forbidden=("trazodone", "100 mg", *_VISITS),
    ),
    EvalCase(
        "thought-records-and-exposure",
        "mixed",
        (
            _goal(
                "Working on social anxiety with CBT. Homework: complete two thought records "
                "about work meetings and attempt one exposure from the hierarchy (asking a "
                "question in the team meeting)."
            ),
            _cadence("Weekly 45-minute therapy; next visit in one week."),
        ),
        expected=(("thought record",), ("exposure",)),
        forbidden=(*_MEDS, "weekly", "next visit", "45-minute"),
    ),
    EvalCase(
        "behavioral-activation-eval",
        "mixed",
        (
            _plan_items(
                "Start sertraline 25 mg daily for one week, then 50 mg.\n"
                "Obtain TSH and CBC.\n"
                "Behavioral activation: schedule one pleasant activity each day and note "
                "mood before and after."
            ),
            _between("Call the clinic with side effects. In an emergency call 911."),
        ),
        expected=(("pleasant activity",),),
        forbidden=_MEDS + _CRISIS + ("side effects",),
    ),
    EvalCase(
        "reading-assignment",
        "mixed",
        (
            _goal(
                "Psychoeducation on the cycle of avoidance. Client will read chapter 3 of "
                "Mind Over Mood before next session."
            ),
        ),
        expected=(("chapter 3",),),
        forbidden=_MEDS,
    ),
    EvalCase(
        "dbt-diary-card",
        "mixed",
        (
            _goal(
                "Skills: distress tolerance (TIPP) reviewed in session. Client will fill out "
                "the DBT diary card daily and practise TIPP when urges reach 7/10."
            ),
            _between("Use the safety plan; call 988 if urges are unmanageable."),
        ),
        expected=(("diary card",), ("tipp",)),
        forbidden=(*_CRISIS, "safety plan"),
    ),
    EvalCase(
        "walking-and-screen-time",
        "mixed",
        (
            _lifestyle(
                "Exercise and screen time discussed. Client agreed to walk 20 minutes on "
                "weekdays and to put the phone away an hour before bed."
            ),
            _goal("Depression: continue supportive therapy and bupropion XL 300 mg."),
        ),
        expected=(("walk",), ("phone",)),
        forbidden=("bupropion", "300 mg", "supportive therapy"),
    ),
    EvalCase(
        "worry-time",
        "mixed",
        (
            _goal(
                "GAD: introduced scheduled worry time. Client to set aside 15 minutes each "
                "evening for worry time and postpone worries outside it. Hydroxyzine 25 mg "
                "as needed continues."
            ),
        ),
        expected=(("worry time",),),
        forbidden=("hydroxyzine", "25 mg"),
    ),
    EvalCase(
        "list-shaped-plan-items",
        "mixed",
        (
            _plan_items(
                "Continue lithium 600 mg nightly.\n"
                "Lithium level and BMP in 5 days.\n"
                "Mood chart daily, including hours slept.\n"
                "Return in 2 weeks."
            ),
        ),
        expected=(("mood chart",),),
        forbidden=("lithium", "bmp", *_VISITS),
    ),
    EvalCase(
        "grounding-and-journal",
        "mixed",
        (
            _goal(
                "Trauma-focused work deferred until stabilisation. Client will practise the "
                "5-4-3-2-1 grounding exercise when distressed and journal for ten minutes "
                "after nightmares."
            ),
            _cadence("Every two weeks while medication is adjusted."),
        ),
        expected=(("grounding",), ("journal",)),
        forbidden=(*_MEDS, "every two weeks"),
    ),
    # --- no homework ------------------------------------------------------
    EvalCase(
        "med-check-only",
        "none",
        (
            _goal("Stable on current regimen. Continue sertraline 100 mg daily."),
            _lifestyle("Diet and exercise reviewed; no changes."),
        ),
        forbidden=_MEDS,
    ),
    EvalCase(
        "labs-and-follow-up",
        "none",
        (
            _plan_items("Obtain A1c and lipid panel.\nFollow up in 4 weeks.\nRefer to PCP."),
            _between("Call the office with questions. For emergencies call 911."),
        ),
        forbidden=_MEDS + _VISITS + _CRISIS + ("a1c", "lipid"),
    ),
    EvalCase(
        "education-without-a-task",
        "none",
        (
            _lifestyle("Discussed the effects of alcohol on sleep and mood."),
            _goal("Supportive therapy focused on grief. Client tearful; processed loss."),
        ),
        forbidden=("alcohol",),
    ),
    EvalCase(
        "clinician-tasks-only",
        "none",
        (
            _goal(
                "Clinician will send the release of information to the school and "
                "coordinate with the PCP about the thyroid results."
            ),
        ),
        forbidden=("release", "coordinate", "thyroid"),
    ),
    EvalCase(
        "placeholder-text",
        "none",
        (_goal("Not discussed this visit."), _lifestyle("None.")),
    ),
    # --- wording that invites a mistake ----------------------------------
    EvalCase(
        "taper-schedule-reads-like-a-task",
        "trap",
        (
            _goal(
                "Client will take clonazepam 0.5 mg at night for one week, then 0.25 mg for "
                "one week, then stop. Client will also practise progressive muscle "
                "relaxation nightly."
            ),
        ),
        expected=(("muscle relaxation",),),
        forbidden=("clonazepam", "0.5 mg", "0.25 mg", "stop"),
    ),
    EvalCase(
        "crisis-steps-are-not-homework",
        "trap",
        (
            _between(
                "If thoughts of suicide return, use the safety plan, call 988 or go to the "
                "nearest emergency department."
            ),
            _goal("Client will continue the gratitude list started last month, three items a day."),
        ),
        expected=(("gratitude",),),
        forbidden=(*_CRISIS, "safety plan", "suicide"),
    ),
    EvalCase(
        "vitals-monitoring-is-not-homework",
        "trap",
        (
            _lifestyle(
                "Client will check blood pressure at home twice weekly while on the new "
                "medication and bring readings. Client agreed to practise mindful eating "
                "at one meal a day."
            ),
        ),
        expected=(("mindful eating",),),
        forbidden=("blood pressure", "readings"),
    ),
    EvalCase(
        "a-past-task-is-not-a-new-one",
        "trap",
        (
            _goal(
                "Client completed the thought records assigned last time and found them "
                "helpful. New homework: identify one cognitive distortion each day."
            ),
        ),
        expected=(("cognitive distortion",),),
        forbidden=("completed",),
    ),
    EvalCase(
        "appointment-booking-is-not-homework",
        "trap",
        (
            _cadence(
                "Client will schedule weekly therapy with the practice and book a sleep "
                "study. Client will practise the breathing app daily."
            ),
        ),
        expected=(("breathing",),),
        forbidden=("schedule weekly", "sleep study", "book"),
    ),
)


def all_cases() -> tuple[EvalCase, ...]:
    return CASES
