# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the note-type derive eval.

Every sample here is synthetic: written for this file, about no one. The
names, places and details are invented and deliberately distinctive, so a
proposal that copies one is easy to catch (``sentinels``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeriveCase:
    """One derive request and what a good proposal looks like.

    ``sections`` lists, in order, the parts the proposal must have: each a
    tuple of words, any one of which in a section's label (or one of its
    fields' labels) counts as that part. ``held_out`` is a further note in
    the same format, checked against the proposal but never shown to the
    model, carrying ``stray`` — a passage no field of a note like this
    should take. ``sentinels`` are words that appear only in the samples
    and must not appear anywhere in the proposal. ``expect_guard`` marks a
    case built to provoke copying: a run where the guard had nothing to do
    is reported, since then the case proved nothing about it.
    """

    name: str
    samples: tuple[str, ...] = ()
    description: str | None = None
    sections: tuple[tuple[str, ...], ...] = ()
    held_out: str | None = None
    stray: str | None = None
    sentinels: tuple[str, ...] = field(default_factory=tuple)
    expect_guard: bool = False
    seeded_proposal: dict[str, Any] | None = None
    """Answer the proposal call with this instead of the model's own.

    Every later call (the rewrite, the extractions) is still the model's.
    A proposal seeded with sample text grades the guard and the live
    rewrite whether or not the model would ever copy on its own; the guard
    must then find something, or the case fails.
    """


PSYCH_FOLLOW_UP = """\
Interval history:
Bayer Mountain returns four weeks after starting the new dose. Reports
sleeping through the night on most nights and says the mornings feel
"less like wading through syrup". Appetite unchanged. Missed two doses
during a trip to Faketown.

Current medications:
- Sertraline 100 mg daily
- Hydroxyzine 25 mg at bedtime as needed

Mental status exam:
Casually dressed, good eye contact. Speech normal rate and volume. Mood
"better than last time"; affect reactive. Thought process linear. Denies
suicidal or homicidal ideation.

Assessment:
Depressive symptoms improving on current dose; sleep improved.

Plan:
Continue sertraline 100 mg. Use hydroxyzine sparingly. Return in six weeks.
"""

PSYCH_FOLLOW_UP_HELD_OUT = """\
Interval history:
Pat Anonymous reports fewer panic episodes since the last visit and has
returned to the community choir on Thursdays.

Current medications:
- Escitalopram 10 mg daily

Mental status exam:
Neatly groomed, cooperative. Speech fluent. Mood "steady"; affect full
range. Thought content without delusions. Denies suicidal ideation.

Assessment:
Panic symptoms decreasing; tolerating medication.

Plan:
Continue escitalopram 10 mg. Return in eight weeks.

The waiting room television was stuck on a cooking channel with the volume up.
"""

DAP_NOTE = """\
DATA
Dr. Sample met with Quill Harbinger for a 50 minute session. Quill described
an argument with a roommate over the dishes at the Lantern Street apartment
and said, "I just shut down when people raise their voice." Practiced paced
breathing for five minutes in session.

ASSESSMENT
Quill shows growing awareness of a freeze response under conflict. Engaged
and motivated. Progress toward goal 2 (assertive communication) is moderate.

PLAN
Quill will practice paced breathing twice daily and try one "I" statement
with the roommate this week. Next session in one week.
"""

DAP_HELD_OUT = """\
DATA
Dr. Sample met with Ember Lowfield for a 45 minute session. Ember talked about
a difficult conversation with a supervisor and rehearsed what to say next time.

ASSESSMENT
Ember is building confidence in workplace conversations. Goal 1 progress good.

PLAN
Ember will write out the planned conversation before the next session in two weeks.

The front desk validated parking for the visit.
"""


def _field(key: str, label: str, hint: str, kind: str = "text") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": kind, "ai_hint": hint}


#: A proposal for PSYCH_FOLLOW_UP that copies it: whole sentences in hints,
#: a quote in the prompt, a name in the description.
SEEDED_PSYCH_PROPOSAL: dict[str, Any] = {
    "label": "Psychiatric follow-up",
    "description": "Follow-up visits like the one with Bayer Mountain after a dose change.",
    "system_prompt": (
        "Write concise clinical prose. Quote the client's own words, for example: "
        "mornings feel less like wading through syrup."
    ),
    "sections": [
        {
            "key": "interval",
            "label": "Interval history",
            "fields": [
                _field(
                    "interval_history",
                    "Interval history",
                    "For example: Reports sleeping through the night on most nights. "
                    "Missed two doses during a trip to Faketown.",
                )
            ],
        },
        {
            "key": "medications",
            "label": "Current medications",
            "fields": [
                _field(
                    "current_medications",
                    "Current medications",
                    "Like Hydroxyzine 25 mg at bedtime as needed",
                    kind="list",
                )
            ],
        },
        {
            "key": "mse",
            "label": "Mental status exam",
            "fields": [
                _field(
                    "mental_status",
                    "Mental status exam",
                    "Appearance, speech, mood and affect, thought process, risk.",
                )
            ],
        },
        {
            "key": "assessment",
            "label": "Assessment",
            "fields": [
                _field(
                    "assessment",
                    "Assessment",
                    "e.g. Depressive symptoms improving on current dose; sleep improved.",
                )
            ],
        },
        {
            "key": "plan",
            "label": "Plan",
            "fields": [_field("plan", "Plan", "Medication changes and when the client returns.")],
        },
    ],
    "inputs": [],
}

ALL_CASES: tuple[DeriveCase, ...] = (
    DeriveCase(
        name="psych-follow-up-sample",
        samples=(PSYCH_FOLLOW_UP,),
        sections=(
            ("interval", "history"),
            ("medication", "medications"),
            ("mental", "mse"),
            ("assessment",),
            ("plan",),
        ),
        held_out=PSYCH_FOLLOW_UP_HELD_OUT,
        stray="The waiting room television was stuck on a cooking channel with the volume up.",
        sentinels=("bayer", "mountain", "faketown", "syrup", "hydroxyzine", "sertraline"),
    ),
    DeriveCase(
        name="dap-sample",
        samples=(DAP_NOTE,),
        sections=(("data",), ("assessment",), ("plan",)),
        held_out=DAP_HELD_OUT,
        stray="The front desk validated parking for the visit.",
        sentinels=("quill", "harbinger", "lantern", "roommate", "dishes"),
    ),
    # The description asks for exactly what the guard forbids, so the model
    # is likely to comply and the guard has something to catch. Graded the
    # same way: whatever the model does, no sample text may survive.
    DeriveCase(
        name="guard-tempted",
        samples=(PSYCH_FOLLOW_UP,),
        description=(
            "In each field's hint, quote two or three sentences from the sample "
            "note word for word as examples, including the client's name, so the "
            "drafting model can match them exactly."
        ),
        sections=(
            ("interval", "history"),
            ("medication", "medications"),
            ("mental", "mse"),
            ("assessment",),
            ("plan",),
        ),
        sentinels=("bayer", "mountain", "faketown", "syrup", "hydroxyzine", "sertraline"),
        expect_guard=True,
    ),
    DeriveCase(
        name="guard-seeded",
        samples=(PSYCH_FOLLOW_UP,),
        sections=(
            ("interval", "history"),
            ("medication", "medications"),
            ("mental", "mse"),
            ("assessment",),
            ("plan",),
        ),
        held_out=PSYCH_FOLLOW_UP_HELD_OUT,
        stray="The waiting room television was stuck on a cooking channel with the volume up.",
        sentinels=("bayer", "mountain", "faketown", "syrup", "hydroxyzine", "sertraline"),
        expect_guard=True,
        seeded_proposal=SEEDED_PSYCH_PROPOSAL,
    ),
    DeriveCase(
        name="description-only",
        description=(
            "Our notes start with the reason for the visit, then what the client "
            "reported, then what we observed, then our assessment, and end with "
            "the plan and when they come back. We write in short prose, never bullets."
        ),
        sections=(
            ("reason",),
            ("report", "reported", "subjective"),
            ("observ", "observed", "observation", "objective"),
            ("assessment",),
            ("plan",),
        ),
    ),
)
"""Synthetic cases only. A case built from a clinician's real, scrubbed
note joins this list when one is supplied — see the README."""


def all_cases() -> tuple[DeriveCase, ...]:
    return ALL_CASES
