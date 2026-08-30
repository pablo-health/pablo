# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The corpus the availability-rule parser is graded against.

Positive cases cover all eight rule types the scheduling engine can
evaluate, including two phrasings of the same type and one sentence that
has to become four rules. Negative cases must be refused rather than
guessed at: a sentence with no boundary to write down, a sentence about
who may book rather than when slots exist, and a sentence that bundles a
rule with an unrelated request.

A confident wrong rule silently blocks or opens a therapist's calendar,
which is worse than falling through to the form — so the corpus is built
so that refusing is never punished and guessing always is.

Relative dates are resolved against a fixed anchor rather than the wall
clock, so the same run produces the same verdict tomorrow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Anchor "today" for the relative-date cases below. A real parse receives
# this from request context; here it is pinned so the corpus is stable.
REFERENCE_DATE = "2026-08-04"


@dataclass(frozen=True)
class ExpectedRule:
    """One rule a case expects, before it is written anywhere.

    Deliberately without an id, owner, or timestamps: those are assigned
    when a therapist confirms a proposal through the ordinary create-rule
    route, never by the parser.
    """

    rule_type: str
    params: dict[str, Any] = field(default_factory=dict)
    enforcement: str = "hard"


@dataclass(frozen=True)
class EvalCase:
    """One phrasing and what the parser should do with it.

    ``expected=None`` means the phrasing must be refused. ``category``
    groups cases for reporting: ``positive``, ``ambiguous``,
    ``out_of_scope``, or ``multi_intent``.
    """

    name: str
    phrasing: str
    description: str
    category: str
    expected: tuple[ExpectedRule, ...] | None


def _positive(name: str, phrasing: str, description: str, *rules: ExpectedRule) -> EvalCase:
    return EvalCase(
        name=name,
        phrasing=phrasing,
        description=description,
        category="positive",
        expected=rules,
    )


def _refuse(name: str, phrasing: str, description: str, category: str) -> EvalCase:
    return EvalCase(
        name=name,
        phrasing=phrasing,
        description=description,
        category=category,
        expected=None,
    )


# ---------------------------------------------------------------------------
# Parseable — one per rule type, an alternate phrasing, and a multi-rule
# sentence.
# ---------------------------------------------------------------------------


def all_cases() -> list[EvalCase]:
    return [
        _positive(
            "no_meetings_on_friday",
            "no meetings on Friday",
            "block_day_of_week, direct phrasing",
            ExpectedRule("block_day_of_week", {"day_of_week": 4}),
        ),
        _positive(
            "dont_work_wednesdays",
            "I don't work Wednesdays",
            "block_day_of_week again, phrased as a habit rather than a prohibition",
            ExpectedRule("block_day_of_week", {"day_of_week": 2}),
        ),
        _positive(
            "nothing_before_ten",
            "nothing before 10",
            "block_time_range covering the unavailable early-morning window",
            ExpectedRule("block_time_range", {"start": "00:00", "end": "10:00"}),
        ),
        _positive(
            "no_sessions_week_of_20th",
            "no sessions the week of the 20th",
            # "The week of the 20th" is read as the calendar week CONTAINING
            # the 20th — Monday through Sunday — not a seven-day span
            # starting on it. Against the anchor, the 20th is a Thursday, so
            # the week is Mon 2026-08-17 to Sun 2026-08-23. Both readings are
            # defensible in English; this one is pinned so the case has a
            # single answer. Refusing the phrase outright stays acceptable.
            "block_date_range over the calendar week containing the 20th",
            ExpectedRule(
                "block_date_range", {"start_date": "2026-08-17", "end_date": "2026-08-23"}
            ),
        ),
        _positive(
            "buffer_between_clients",
            "15 minutes between clients",
            "buffer_before and buffer_after together — either alone leaves one "
            "booking order unguarded",
            ExpectedRule("buffer_before", {"minutes": 15}),
            ExpectedRule("buffer_after", {"minutes": 15}),
        ),
        _positive(
            "max_six_a_day",
            "max 6 a day",
            "max_per_day",
            ExpectedRule("max_per_day", {"max": 6}),
        ),
        _positive(
            "out_dec_24_25",
            "I'm out Dec 24 and 25",
            "block_specific_dates, given a year by the anchor",
            ExpectedRule("block_specific_dates", {"dates": ["2026-12-24", "2026-12-25"]}),
        ),
        _positive(
            "nine_to_five_mon_thu",
            "9 to 5 Monday through Thursday",
            "one working_hours rule per day in the range, never one rule spanning days",
            ExpectedRule("working_hours", {"day_of_week": 0, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 1, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 2, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 3, "start": "09:00", "end": "17:00"}),
        ),
        # -------------------------------------------------------------------
        # Must refuse.
        # -------------------------------------------------------------------
        _refuse(
            "ambiguous_not_too_early",
            "not too early",
            "no concrete cutoff — a guessed boundary blocks time the therapist wanted",
            "ambiguous",
        ),
        _refuse(
            "ambiguous_afternoon_i_guess",
            "sometime in the afternoon, I guess",
            "hedged, and 'afternoon' has no fixed start and end to write down",
            "ambiguous",
        ),
        _refuse(
            "out_of_scope_no_new_patients",
            "no new patients on Fridays",
            "about WHO may book, not when slots exist — and it wears the same "
            "'no ... on <day>' clothes as a real day block, which is the trap",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_insurance_mondays",
            "I only take insurance clients on Mondays",
            "about WHICH clients, not when the therapist works",
            "out_of_scope",
        ),
        _refuse(
            "multi_intent_invoice",
            "no sessions on Fridays and also send me last month's invoice",
            "a real rule bundled with an unrelated request — parsing half of it is "
            "still a guess, and the dropped half leaves no trace",
            "multi_intent",
        ),
        _refuse(
            "multi_intent_cancel_today",
            "I don't work Wednesdays, can you also cancel my 3pm today?",
            "a rule bundled with an immediate action on a specific appointment",
            "multi_intent",
        ),
    ]
