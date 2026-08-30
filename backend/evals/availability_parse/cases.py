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
    ``out_of_scope``, ``date_token_gap``, or ``multi_intent``.

    ``expected_exclusive`` grades the parser's top-level ``exclusive``
    flag (set when a sentence states a complete set of working hours,
    e.g. "I ONLY meet Mondays and Tuesdays") the same way enforcement is
    graded: a soft finding, never gated. Left ``None`` (the default) on
    every case that doesn't turn on exclusivity, which skips the check
    entirely rather than asserting ``False`` on cases that never
    considered the question.
    """

    name: str
    phrasing: str
    description: str
    category: str
    expected: tuple[ExpectedRule, ...] | None
    expected_exclusive: bool | None = None


def _positive(
    name: str,
    phrasing: str,
    description: str,
    *rules: ExpectedRule,
    expected_exclusive: bool | None = None,
) -> EvalCase:
    return EvalCase(
        name=name,
        phrasing=phrasing,
        description=description,
        category="positive",
        expected=rules,
        expected_exclusive=expected_exclusive,
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
        # ---------------------------------------------------------------
        # Phrasing breadth — natural alternate phrasings, spanning most
        # rule types, including cases where the surface words point at
        # the wrong rule type (or the wrong number of rules) entirely.
        # ---------------------------------------------------------------
        _positive(
            "fridays_for_paperwork",
            "Fridays are for paperwork",
            "block_day_of_week via a work-allocation framing rather than an explicit "
            "negation — same full-day-block outcome as no_meetings_on_friday",
            ExpectedRule("block_day_of_week", {"day_of_week": 4}),
        ),
        _refuse(
            "mornings_only_tuesdays",
            "mornings only on Tuesdays",
            "'morning' has no fixed end time (11/12/1 are all defensible) — reads like "
            "working_hours but the upper boundary can't be derived without inventing one",
            "ambiguous",
        ),
        _positive(
            "done_by_three",
            "I'm done by 3",
            "block_time_range with no day named — a blanket every-day cutoff, not "
            "working_hours (which needs a day_of_week this sentence doesn't give)",
            ExpectedRule("block_time_range", {"start": "15:00", "end": "23:59"}),
        ),
        _positive(
            "no_more_than_four_a_day",
            "no more than four clients a day",
            "max_per_day, spelled-out number and 'no more than' instead of 'max'",
            ExpectedRule("max_per_day", {"max": 4}),
        ),
        _positive(
            "cap_five_a_day",
            "cap me at five a day",
            "max_per_day again — 'cap me at' is a distinct idiom from 'max'/'no more "
            "than', combined with a spelled-out number",
            ExpectedRule("max_per_day", {"max": 5}),
        ),
        _refuse(
            "leave_a_gap_after_each_session",
            "leave a gap after each session",
            "the trap: the surface word 'after' points a shallow parser at buffer_after "
            "alone, but no minutes are given at all — must refuse for the missing "
            "quantity, not emit buffer_after (wrong: incomplete, the same either-alone "
            "gap buffer_between_clients exists to catch) or invent a number",
            "ambiguous",
        ),
        _refuse(
            "no_back_to_back_without_asking",
            "don't book anyone back to back without asking me first",
            "buffer-shaped ('back to back') but names no minutes, and 'without asking "
            "me first' is an approval-process clause a buffer rule can't encode even "
            "with a number — a live check-in isn't an availability constraint",
            "out_of_scope",
        ),
        _positive(
            "weekends_off_limits",
            "weekends are off-limits",
            "block_day_of_week x2 — 'weekends' names no day directly, requiring the "
            "parser to expand a colloquial term into both Saturday and Sunday",
            ExpectedRule("block_day_of_week", {"day_of_week": 5}),
            ExpectedRule("block_day_of_week", {"day_of_week": 6}),
        ),
        _positive(
            "only_until_noon_wednesdays",
            "I only see clients until noon on Wednesdays",
            "working_hours, not block_time_range — block_time_range has no day_of_week "
            "field, so a day-scoped cutoff can only be expressed as working_hours with "
            "an implicit 00:00 lower bound",
            ExpectedRule("working_hours", {"day_of_week": 2, "start": "00:00", "end": "12:00"}),
        ),
        _refuse(
            "half_a_day_wednesdays",
            "half a day on Wednesdays",
            "'half a day' doesn't say which half — a morning-working or "
            "afternoon-working reading are equally defensible",
            "ambiguous",
        ),
        _positive(
            "back_to_back_fine_ten_before",
            "back-to-back is fine, just give me ten minutes before each new client",
            "buffer_before ONLY — 'back-to-back is fine' explicitly rules out "
            "buffer_after, so emitting both (as buffer_between_clients correctly does "
            "when nothing rules either out) would be wrong here, not merely incomplete",
            ExpectedRule("buffer_before", {"minutes": 10}),
        ),
        _positive(
            "half_hour_between_clients",
            "half an hour between clients",
            "buffer_before + buffer_after, unit conversion (half an hour -> 30 minutes) "
            "on top of buffer_between_clients' fan-out",
            ExpectedRule("buffer_before", {"minutes": 30}),
            ExpectedRule("buffer_after", {"minutes": 30}),
        ),
        _positive(
            "ten_after_every_session",
            "give me 10 after every session to write notes",
            "buffer_after ONLY — directional, completing the buffer trio with "
            "back_to_back_fine_ten_before (before-only) and buffer_between_clients "
            "(both)",
            ExpectedRule("buffer_after", {"minutes": 10}),
        ),
        _positive(
            "mon_wed_8_to_noon",
            "mondays and wednesdays i see clients 8 to noon",
            "working_hours x2, lowercase and uncapitalized day names — two "
            "non-contiguous days in one sentence rather than a range",
            ExpectedRule("working_hours", {"day_of_week": 0, "start": "08:00", "end": "12:00"}),
            ExpectedRule("working_hours", {"day_of_week": 2, "start": "08:00", "end": "12:00"}),
        ),
        # ---------------------------------------------------------------
        # Time edges — noon, midnight, an implicit-pm bare hour, bare
        # 24-hour notation, and ranges crossing lunch.
        # ---------------------------------------------------------------
        _positive(
            "nothing_after_noon",
            "nothing after noon",
            "block_time_range, 'noon' as an unambiguous literal boundary (12:00)",
            ExpectedRule("block_time_range", {"start": "12:00", "end": "23:59"}),
        ),
        _positive(
            "midnight_to_six",
            "I don't take sessions between midnight and 6am",
            "block_time_range, 'midnight' as an unambiguous literal boundary (00:00)",
            ExpectedRule("block_time_range", {"start": "00:00", "end": "06:00"}),
        ),
        _positive(
            "nothing_before_nine_or_after_five",
            "nothing before 9 or after 5",
            "two block_time_range rules from one sentence — bare '5' resolves to 17:00 "
            "by the same evening-hour convention nine_to_five_mon_thu already uses",
            ExpectedRule("block_time_range", {"start": "00:00", "end": "09:00"}),
            ExpectedRule("block_time_range", {"start": "17:00", "end": "23:59"}),
        ),
        _positive(
            "nothing_after_17",
            "nothing after 17",
            "block_time_range from bare 24-hour notation — no colon, no am/pm marker, "
            "and 'after' forces a time reading (no other unit fits 'nothing after 17')",
            ExpectedRule("block_time_range", {"start": "17:00", "end": "23:59"}),
        ),
        _positive(
            "lunch_crossing_range",
            "no sessions between 11:30 and 1:30",
            "block_time_range spanning noon — the second bound has no am/pm marker but "
            "'between 11:30 and 1:30' with no further qualifier only has one sane "
            "reading (11:30am-1:30pm); reading it as 11:30am-1:30am would be a "
            "~14-hour span crossing midnight, which nothing in the sentence supports",
            ExpectedRule("block_time_range", {"start": "11:30", "end": "13:30"}),
        ),
        _positive(
            "block_lunch_12_to_1",
            "block off lunch, 12 to 1",
            "block_time_range, a fully-numbered lunch block with no am/pm inference "
            "needed at all — '12' can only be noon and '1' immediately after it can "
            "only be 1pm, unlike lunch_crossing_range's mixed-precision bounds",
            ExpectedRule("block_time_range", {"start": "12:00", "end": "13:00"}),
        ),
        # ---------------------------------------------------------------
        # Soft preference language — enforcement=soft where a concrete
        # rule is still derivable under the hedge; refusal where the
        # hedge also swallows the only concrete boundary.
        # ---------------------------------------------------------------
        _positive(
            "rather_not_book_after_six",
            "I'd rather not book after 6pm",
            "block_time_range with enforcement=soft — concrete boundary (18:00) plus a "
            "hedge marker ('I'd rather not') that is this corpus's convention for soft "
            "rather than hard",
            ExpectedRule("block_time_range", {"start": "18:00", "end": "23:59"}, "soft"),
        ),
        _positive(
            "prefer_not_more_than_five",
            "I'd prefer not to see more than five clients a day",
            "max_per_day with enforcement=soft — same hedge convention as "
            "rather_not_book_after_six, applied to a different rule type",
            ExpectedRule("max_per_day", {"max": 5}, "soft"),
        ),
        _positive(
            "prefer_fridays_free_of_sessions",
            "I'd prefer to keep Fridays free of sessions",
            "block_day_of_week with enforcement=soft — same hedge convention, "
            "brackets prefer_fridays_light below: a hedge over a COMPLETE boundary "
            "('Fridays', a whole day) parses soft; a hedge with no boundary refuses",
            ExpectedRule("block_day_of_week", {"day_of_week": 4}, "soft"),
        ),
        _refuse(
            "prefer_fridays_light",
            "prefer to keep Fridays light if possible",
            "hedged past derivability — unlike prefer_fridays_free_of_sessions right "
            "above, there is no concrete boundary underneath the hedge ('light' names "
            "no max_per_day count), so the soft-enforcement reading has nothing to "
            "attach to",
            "ambiguous",
        ),
        # ---------------------------------------------------------------
        # Ambiguity and inversion traps — a one-time/recurring split with
        # no tiebreaker, a constraint that needs an invented duration, a
        # positive availability statement a day-proximity matcher would
        # misread as a block, and plain sentiment.
        # ---------------------------------------------------------------
        _refuse(
            "block_out_friday_ambiguous",
            "block out Friday",
            "unlike no_meetings_on_friday, gives no signal whether this is a one-time "
            "block or a standing block_day_of_week rule — both are defensible and nothing "
            "picks one, unlike the pinned-by-design 'next Friday' date resolution",
            "ambiguous",
        ),
        _refuse(
            "last_appointment_starts_at_four",
            "my last appointment starts at 4",
            "a latest-START constraint, not a latest-END — any block_time_range or "
            "working_hours encoding has to invent a session duration to know when 4pm "
            "plus that appointment actually ends",
            "ambiguous",
        ),
        _refuse(
            "fridays_work_great_inversion_trap",
            "Fridays work great for me",
            "the inversion trap: a POSITIVE availability statement (Friday is a good "
            "day) that a day-proximity matcher with no sentiment check would confidently "
            "invert into blocking the therapist's favorite day",
            "ambiguous",
        ),
        _refuse(
            "i_hate_mondays_sentiment",
            "I hate Mondays",
            "sentiment, not an instruction — no rule, no boundary, nothing to encode",
            "ambiguous",
        ),
        # ---------------------------------------------------------------
        # Out-of-scope traps — each shares surface vocabulary with a real
        # rule type but means something none of the eight types can
        # express: who/what/how clients are seen, or a recurrence
        # pattern no rule type has a field for.
        # ---------------------------------------------------------------
        _refuse(
            "out_of_scope_no_couples_fridays",
            "no couples work on Fridays",
            "session-type policy (which format, not when) — shares 'no ... on <day>' "
            "surface form with block_day_of_week, but individual sessions are still "
            "fine that day",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_no_telehealth_mondays",
            "no telehealth on Mondays",
            "modality policy (in-person only that day), not a day block — the day "
            "isn't closed, one delivery format is",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_cash_weekends",
            "cash clients only on weekends",
            "payment-method policy — names days but restricts by how a client pays, "
            "not whether the therapist is available",
            "out_of_scope",
        ),
        _refuse(
            "no_cash_pay_after_five",
            "no cash-pay clients after 5",
            "payment policy wearing block_time_range clothes — a different hook from "
            "out_of_scope_cash_weekends (day-restricted vs. time-restricted), same "
            "underlying trap: it restricts by how a client pays, not availability",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_no_new_until_march",
            "not taking anyone new until March",
            "caseload/intake policy (who can book) with a date reference but no "
            "availability boundary at all — every existing slot is still open",
            "out_of_scope",
        ),
        _refuse(
            "no_new_intakes_december",
            "no new intakes in December",
            "intake policy wearing block_date_range clothes — a different hook from "
            "out_of_scope_no_new_until_march (bounded window vs. open-ended), same "
            "underlying trap: who can book, not when the therapist works",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_every_other_friday",
            "every other Friday off",
            "a recurring biweekly day-block, which no rule type expresses — "
            "block_day_of_week fires every week unconditionally with no "
            "recurrence-interval field, and the date-range/specific-dates types are "
            "one-off, not recurring; mentions 'Friday' but the schema has no correct "
            "answer to give",
            "out_of_scope",
        ),
        _refuse(
            "first_monday_admin_day",
            "first Monday of the month is my admin day",
            "a monthly-cadence recurrence, same family as out_of_scope_every_other_"
            "friday — no rule type expresses 'first <weekday> of every month'. Bonus "
            "trap: against the anchor, the next first Monday (Sep 7, 2026) is Labor Day, "
            "so a parser that resolves it as a single date would coincidentally look "
            "plausible while still being the wrong rule shape entirely",
            "out_of_scope",
        ),
        _refuse(
            "out_of_scope_extra_time_new_clients",
            "give me extra time with new clients",
            "per-client-type session-length policy, not a buffer — buffer_before/"
            "buffer_after are uniform gaps between any two bookings, not conditional "
            "on who the client is; shares 'extra time' vocabulary with the buffer "
            "rule types without being one",
            "out_of_scope",
        ),
        _refuse(
            "wednesday_client_moved_to_thursday",
            "my Wednesday client asked to move to Thursday",
            "a reported request about one client, not an availability policy — two "
            "day names and zero rules; distinct from the other out-of-scope traps, "
            "which are all POLICY statements rather than a narrated one-off event",
            "out_of_scope",
        ),
        # ---------------------------------------------------------------
        # Multi-intent — a rule plus something else in the same sentence
        # must refuse wholesale; a second legitimate rule doesn't rescue
        # it either.
        # ---------------------------------------------------------------
        _refuse(
            "multi_intent_cancellation_question",
            "no sessions on Saturdays, what's my cancellation policy again?",
            "availability rule bundled with an unrelated question, same bundling risk "
            "as multi_intent_invoice with a question instead of a request",
            "multi_intent",
        ),
        _refuse(
            "multi_intent_reschedule_request",
            "I'm out December 24th and 25th, also can you move my 2pm Thursday to Friday?",
            "the same date fragment as out_dec_24_25, bundled with a reschedule "
            "request — the parseable half doesn't rescue the sentence",
            "multi_intent",
        ),
        _refuse(
            "multi_intent_two_rules_one_question",
            "no Mondays, nothing after 4, and by the way what's the address for my "
            "next CE training?",
            "two legitimate, complete availability rules bundled with one unrelated "
            "question — completeness of the rule portion does not rescue a "
            "multi-intent sentence; must refuse wholesale rather than parse the two "
            "rules and silently drop the question",
            "multi_intent",
        ),
        _refuse(
            "multi_intent_reschedule_whoevers_on_it",
            "block off next Friday and reschedule whoever's on it",
            "a rule bundled with an immediate action, same shape as "
            "multi_intent_cancel_today but the action targets whoever the rule "
            "itself would displace",
            "multi_intent",
        ),
        _refuse(
            "multi_intent_buffers_plus_cancel_group",
            "9 to 5 Mondays, 15 minute buffers, and cancel Thursday's group session",
            "the strongest partial-parse temptation in the corpus: two clean, "
            "independently-correct rules (working_hours, buffer_before+buffer_after) "
            "plus one immediate action — must still refuse wholesale",
            "multi_intent",
        ),
        # ---------------------------------------------------------------
        # Dictation noise — filler words, missing punctuation, and
        # lowercase must not change the expected parse from the clean
        # phrasing it reuses.
        # ---------------------------------------------------------------
        _positive(
            "dictation_no_meetings_fridays",
            "um, no meetings on fridays please",
            "same expected parse as no_meetings_on_friday — leading filler, lowercase "
            "day name, trailing politeness word",
            ExpectedRule("block_day_of_week", {"day_of_week": 4}),
        ),
        _positive(
            "dictation_buffer_between_clients",
            "so like fifteen minutes between clients okay",
            "same expected parse as buffer_between_clients — filler before and after "
            "the content, spelled-out number, no punctuation",
            ExpectedRule("buffer_before", {"minutes": 15}),
            ExpectedRule("buffer_after", {"minutes": 15}),
        ),
        _positive(
            "dictation_nothing_before_ten",
            "nothing before ten oclock please",
            "same expected parse as nothing_before_ten — spelled-out number, missing "
            "apostrophe, trailing politeness word",
            ExpectedRule("block_time_range", {"start": "00:00", "end": "10:00"}),
        ),
        _positive(
            "dictation_no_clients_thursdays",
            "um, so, yeah — no clients on Thursdays please",
            "block_day_of_week, heavy false-start filler ('um, so, yeah —') plus a "
            "trailing politeness word — the noisiest phrasing in the corpus for its "
            "rule type",
            ExpectedRule("block_day_of_week", {"day_of_week": 3}),
        ),
        # ---------------------------------------------------------------
        # Date-bearing — an explicit list, an explicit range, a
        # design-pinned next-weekday resolution, a year-crossing range, a
        # passed date rolling to next year, and a named-holiday refusal
        # the date-token design exists to produce.
        # ---------------------------------------------------------------
        _positive(
            "out_next_friday",
            "I'm out next Friday",
            # date_intent.py's weekday resolver pins "next <weekday>" to
            # THIS week's occurrence plus a full 7 days, always -- never
            # the plain English "nearest upcoming Friday" reading. Against
            # the anchor (Tue 2026-08-04), the plain reading would give
            # 2026-08-07; the pinned resolver gives 2026-08-14. The corpus
            # expects the pinned value: a design-pinned ambiguity grades as
            # parseable at the pinned value, not at whichever reading an
            # English speaker would guess.
            "block_specific_dates, 'next Friday' resolved to the design-pinned value "
            "(this week's Friday + 7 days), not the plain-English nearest Friday",
            ExpectedRule("block_specific_dates", {"dates": ["2026-08-14"]}),
        ),
        _positive(
            "out_this_and_next_friday",
            "take me off the books this Friday and next Friday",
            "block_specific_dates, two weekday tokens with different modifiers in one "
            "list — 'this Friday' (no modifier) resolves to the nearest Friday, 'next "
            "Friday' to the pinned value 7 days beyond it",
            ExpectedRule("block_specific_dates", {"dates": ["2026-08-07", "2026-08-14"]}),
        ),
        _positive(
            "out_explicit_list_named_month",
            "I'm out September 4th and September 11th",
            "block_specific_dates, an explicit list naming the month on both dates "
            "(avoiding the 'which month' ambiguity a bare day-of-month list would "
            "have) — both dates happen to be Fridays, which the parser must not "
            "collapse into a block_day_of_week guess",
            ExpectedRule("block_specific_dates", {"dates": ["2026-09-04", "2026-09-11"]}),
        ),
        _positive(
            "explicit_range_named_months",
            "I'm out March 3 through March 10",
            "block_date_range from an explicit named-month range — nearest March "
            "after the anchor is March 2027, since March 2026 has already passed",
            ExpectedRule(
                "block_date_range", {"start_date": "2027-03-03", "end_date": "2027-03-10"}
            ),
        ),
        _positive(
            "out_dec23_to_jan2",
            "no appointments from December 23rd to January 2nd",
            "block_date_range crossing a year boundary — December resolves within "
            "the anchor year (2026-12-23, still ahead of the anchor), January "
            "resolves against the anchor independently and rolls forward "
            "(2027-01-02, since plain 2026-01-02 has already passed)",
            ExpectedRule(
                "block_date_range", {"start_date": "2026-12-23", "end_date": "2027-01-02"}
            ),
        ),
        _positive(
            "out_july_4th_rolls_year",
            "I'm off July 4th",
            "block_specific_dates, a single explicit date earlier in the calendar "
            "than the anchor (July before August) rolls to the following year: "
            "2027-07-04, not 2026-07-04",
            ExpectedRule("block_specific_dates", {"dates": ["2027-07-04"]}),
        ),
        _positive(
            "out_this_thursday",
            "I'm out this Thursday",
            "block_specific_dates, a bare/'this'-modified weekday resolves to the "
            "nearest occurrence on or after the anchor — 2026-08-06, two days out",
            ExpectedRule("block_specific_dates", {"dates": ["2026-08-06"]}),
        ),
        _refuse(
            "date_token_gap_named_holidays",
            "closed between Christmas and New Year's",
            "the dates are real and well-defined, but the parser's own system prompt "
            "forbids resolving a holiday name from world knowledge — it must emit an "
            "explicit MM-DD or weekday token or refuse, and neither of those is a "
            "holiday name; distinct from 'ambiguous' (there is exactly one correct "
            "reading, the tokenizer just isn't allowed to reach it) and from "
            "'out_of_scope' (block_date_range is the right rule type; the gap is "
            "upstream, in date resolution, not rule-type selection)",
            "date_token_gap",
        ),
        _refuse(
            "off_for_thanksgiving_week",
            "I'm off for Thanksgiving week",
            "the same date-token gap as date_token_gap_named_holidays — a named "
            "holiday with no explicit month-day or weekday token to carry it, plus "
            "'week' reintroducing the same span-boundary ambiguity as "
            "no_sessions_week_of_20th on top of the unresolvable name",
            "date_token_gap",
        ),
        # ---------------------------------------------------------------
        # Compound multi-rule — two rules where dropping either is a
        # hard fail, a three-rule sentence spanning three rule types, an
        # override fan-out, and the exclusive flag.
        # ---------------------------------------------------------------
        _positive(
            "no_mondays_and_nothing_after_four",
            "no Mondays and nothing after 4",
            "block_day_of_week + block_time_range from one sentence — dropping either "
            "rule silently opens time the therapist meant blocked",
            ExpectedRule("block_day_of_week", {"day_of_week": 0}),
            ExpectedRule("block_time_range", {"start": "16:00", "end": "23:59"}),
        ),
        _positive(
            "three_rule_sentence",
            "no sessions on Mondays, nothing after 4, and no more than five clients a day",
            "three different rule types (block_day_of_week + block_time_range + "
            "max_per_day) from one sentence — the multi-rule case the corpus "
            "previously only tested with four rules of the SAME type "
            "(nine_to_five_mon_thu)",
            ExpectedRule("block_day_of_week", {"day_of_week": 0}),
            ExpectedRule("block_time_range", {"start": "16:00", "end": "23:59"}),
            ExpectedRule("max_per_day", {"max": 5}),
        ),
        _positive(
            "nine_to_five_weekdays_except_wed_noon",
            "9 to 5 weekdays, but Wednesdays I stop at noon",
            "five working_hours rules — the general 9-5 rule fanned out over every "
            "weekday EXCEPT Wednesday, which gets its own overriding rule instead of "
            "a second, contradictory 9-5 rule; five 9-5 rules or a double Wednesday "
            "entry are both wrong sets",
            ExpectedRule("working_hours", {"day_of_week": 0, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 1, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 2, "start": "09:00", "end": "12:00"}),
            ExpectedRule("working_hours", {"day_of_week": 3, "start": "09:00", "end": "17:00"}),
            ExpectedRule("working_hours", {"day_of_week": 4, "start": "09:00", "end": "17:00"}),
        ),
        _positive(
            "only_tue_thu_10_to_4_exclusive",
            "I only see clients Tuesdays and Thursdays, 10 to 4",
            "working_hours x2 plus the exclusive flag — 'I only see clients ...' is "
            "the system prompt's own example trigger for exclusive=true, meaning "
            "these two rules together are the therapist's complete working hours, "
            "not merely two days among others left unconstrained",
            ExpectedRule("working_hours", {"day_of_week": 1, "start": "10:00", "end": "16:00"}),
            ExpectedRule("working_hours", {"day_of_week": 3, "start": "10:00", "end": "16:00"}),
            expected_exclusive=True,
        ),
    ]
