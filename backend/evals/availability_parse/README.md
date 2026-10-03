# Availability-rule parser eval

A therapist can describe their availability in a sentence — "no meetings
on Friday", "9 to 5 Monday through Thursday" — and
`app.services.availability_parse_service` maps it onto the rule types the
scheduling engine can actually evaluate. Every proposal it returns still
has to be confirmed by a person; the parser never writes a rule.

That makes the interesting question not "how often is it right" but "how
often is it *confidently wrong*", and this corpus is how that gets a
number instead of an impression.

## What it grades

The parser is graded as `sentence -> rules | refusal`. The target schema
mirrors `app/scheduling_engine/models/availability.py`'s `RuleType` and
the `params` keys the checkers in
`app/scheduling_engine/services/availability.py` actually read — so
nothing in the corpus asks for a rule the engine can't evaluate.

The corpus is 89 cases: the original 14, a 60-case expansion drafted in
two independent passes and merged by arbitration (see "On this expansion"
below), 10 cases covering appointment types and weekly caps, and 5
covering day-less hours and caps tied to a weekday. 47 are parseable, 42
must refuse (52.8% / 47.2%).

Two soft checks grade how helpful a correct refusal is, never gated: an
ambiguous case marked `expects_two_readings` should come back with both
readings for the therapist to pick from, and an unknown-type case with
`expected_unknown_type` should name the missing kind so the screen can
offer to add it. A case that also pins `expected_readings` grades what
the readings say, and that check is hard: the therapist saves whichever
reading they pick, so a wrong one is a wrong rule set.

Every case also records how long its parse took, and the summary reports
p50, p95 and max across the run (see "Latency" below).

### Case matrix

Grouped the same way `cases.py` groups them, so the two stay easy to keep
in sync.

**Original 14** — one per rule type, an alternate phrasing, a four-rule
sentence, and six refusals.

| case | category | expects |
|---|---|---|
| `no_meetings_on_friday` | parseable | `block_day_of_week` |
| `dont_work_wednesdays` | parseable | `block_day_of_week`, other phrasing |
| `nothing_before_ten` | parseable | `block_time_range` 00:00–10:00 |
| `no_sessions_week_of_20th` | parseable | `block_date_range` |
| `buffer_between_clients` | parseable | `buffer_before` + `buffer_after` |
| `max_six_a_day` | parseable | `max_per_day` |
| `out_dec_24_25` | parseable | `block_specific_dates` |
| `nine_to_five_mon_thu` | parseable | four `working_hours` rules |
| `ambiguous_not_too_early` | ambiguous | refuse |
| `ambiguous_afternoon_i_guess` | ambiguous | refuse |
| `out_of_scope_no_new_patients` | out of scope | refuse — a booking policy |
| `out_of_scope_insurance_mondays` | out of scope | refuse — an intake policy |
| `multi_intent_invoice` | multi-intent | refuse — a rule plus a request |
| `multi_intent_cancel_today` | multi-intent | refuse — a rule plus an action |

**Phrasing breadth** — natural alternate phrasings across most rule
types, including cases where the surface words point at the wrong rule
type (or the wrong rule *count*) entirely.

| case | expects |
|---|---|
| `fridays_for_paperwork` | `block_day_of_week` |
| `mornings_only_tuesdays` | refuse — no fixed end for "morning" |
| `done_by_three` | `block_time_range`, no day named |
| `no_more_than_four_a_day` | `max_per_day` |
| `cap_five_a_day` | `max_per_day`, a distinct idiom |
| `leave_a_gap_after_each_session` | refuse — "after" is a trap, no minutes given |
| `no_back_to_back_without_asking` | refuse — approval clause, not a buffer |
| `weekends_off_limits` | two `block_day_of_week` rules |
| `only_until_noon_wednesdays` | refuse — upper bound only, no stated start to encode |
| `half_a_day_wednesdays` | refuse — which half is undecidable |
| `back_to_back_fine_ten_before` | `buffer_before` only |
| `half_hour_between_clients` | `buffer_before` + `buffer_after`, unit conversion |
| `ten_after_every_session` | `buffer_after` only |
| `mon_wed_8_to_noon` | two `working_hours` rules, lowercase |

**Time edges** — noon, midnight, an implicit-pm bare hour, bare 24-hour
notation, and ranges crossing lunch.

| case | expects |
|---|---|
| `nothing_after_noon` | `block_time_range` 12:00–23:59 |
| `midnight_to_six` | `block_time_range` 00:00–06:00 |
| `nothing_before_nine_or_after_five` | two `block_time_range` rules |
| `nothing_after_17` | `block_time_range` 17:00–23:59, bare 24h |
| `lunch_crossing_range` | `block_time_range` 11:30–13:30 |
| `block_lunch_12_to_1` | `block_time_range` 12:00–13:00, no am/pm inference needed |

**Soft preference language** — `enforcement=soft` where a concrete rule
is still derivable under the hedge; refusal where the hedge also
swallows the only concrete boundary. See "Soft-enforcement vocabulary"
below.

| case | expects |
|---|---|
| `rather_not_book_after_six` | `block_time_range`, soft |
| `prefer_not_more_than_five` | `max_per_day`, soft |
| `prefer_fridays_free_of_sessions` | `block_day_of_week`, soft |
| `prefer_fridays_light` | refuse — hedge with no boundary underneath |

**Ambiguity and inversion traps** — a one-time/recurring split with no
tiebreaker, a constraint needing an invented duration, a positive
statement a naive matcher would invert, and plain sentiment.

| case | expects |
|---|---|
| `block_out_friday_ambiguous` | refuse — one-time vs. recurring, no tiebreaker |
| `last_appointment_starts_at_four` | refuse — latest-start, not latest-end |
| `fridays_work_great_inversion_trap` | refuse — a positive statement, not a block |
| `i_hate_mondays_sentiment` | refuse — sentiment, not an instruction |

**Out-of-scope traps** — the load-bearing category. Each shares surface
vocabulary with a real rule type but means something none of the eight
types can express.

| case | trap |
|---|---|
| `out_of_scope_no_couples_fridays` | session-type policy, not a day block |
| `out_of_scope_no_telehealth_mondays` | modality policy, not a day block |
| `out_of_scope_cash_weekends` | payment policy, day-shaped |
| `no_cash_pay_after_five` | payment policy, time-range-shaped |
| `out_of_scope_no_new_until_march` | caseload policy, open-ended |
| `no_new_intakes_december` | caseload policy, date-range-shaped |
| `out_of_scope_every_other_friday` | biweekly cadence, no rule type for it |
| `first_monday_admin_day` | monthly cadence, no rule type for it |
| `out_of_scope_extra_time_new_clients` | per-client-type policy, buffer-shaped |
| `wednesday_client_moved_to_thursday` | a reported one-off event, not a policy |

**Multi-intent** — a rule plus something else must refuse wholesale; a
second legitimate rule doesn't rescue it either.

| case | bundles |
|---|---|
| `multi_intent_cancellation_question` | rule + unrelated question |
| `multi_intent_reschedule_request` | rule + reschedule request |
| `multi_intent_two_rules_one_question` | two complete rules + one question |
| `multi_intent_reschedule_whoevers_on_it` | rule + action on what the rule displaces |
| `multi_intent_buffers_plus_cancel_group` | two clean rules + an action |

**Dictation noise** — filler words, missing punctuation, and lowercase
must not change the expected parse from the clean phrasing each one
reuses.

| case | reuses the expected parse of |
|---|---|
| `dictation_no_meetings_fridays` | `no_meetings_on_friday` |
| `dictation_buffer_between_clients` | `buffer_between_clients` |
| `dictation_nothing_before_ten` | `nothing_before_ten` |
| `dictation_no_clients_thursdays` | (new: heaviest filler in the corpus) |

**Date-bearing** — an explicit list, an explicit range, design-pinned
weekday resolution, a year-crossing range, a passed date rolling
forward, and the date-token gap. See "Design-pinned vs. unpinned
ambiguity" below for the weekday-resolution cases specifically.

| case | expects |
|---|---|
| `out_next_friday` | `block_specific_dates`, pinned +7-day resolution |
| `out_this_and_next_friday` | `block_specific_dates`, two modifiers in one list |
| `out_explicit_list_named_month` | `block_specific_dates`, explicit list |
| `explicit_range_named_months` | `block_date_range`, rolls to next year |
| `out_dec23_to_jan2` | `block_date_range`, crosses a year boundary |
| `out_july_4th_rolls_year` | `block_specific_dates`, passed date rolls forward |
| `out_this_thursday` | `block_specific_dates`, nearest occurrence |
| `date_token_gap_named_holidays` | refuse — the tokenizer has no holiday-name slot |
| `off_for_thanksgiving_week` | refuse — same gap, plus a week-span ambiguity |

**Compound multi-rule** — two rules where dropping either is a hard
fail, a three-type sentence, an override fan-out, and the `exclusive`
flag.

| case | expects |
|---|---|
| `no_mondays_and_nothing_after_four` | `block_day_of_week` + `block_time_range` |
| `three_rule_sentence` | three different rule types |
| `nine_to_five_weekdays_except_wed_noon` | five `working_hours` rules, one overriding |
| `only_tue_thu_10_to_4_exclusive` | two `working_hours` rules + `exclusive=true` |

**Appointment types and weekly caps** — a rule scoped to one of the
practice's own types, and the readings that are not the parser's to
settle. The corpus's practice has three types: `Intake`, `Consultation`
and `Session`. A case here grades the bound `appointment_type_id`
exactly, so a rule that comes back unbound — silently applying to every
kind of appointment — is a hard failure, not a near miss.

| case | expects |
|---|---|
| `two_intakes_a_week` | `max_per_week` scoped to `Intake` |
| `no_more_than_twenty_a_week` | `max_per_week`, practice-wide |
| `one_consultation_a_day` | `max_per_day` scoped to `Consultation` |
| `intakes_tuesday_afternoons` | `working_hours` scoped to `Intake`, open to other types |
| `tuesday_afternoons_intakes_only` | the same window, claimed for `Intake` alone |
| `only_intakes_on_tuesdays` | refuse — narrowing or claim, and they store different rules |
| `two_intakes_a_week_on_tuesdays` | refuse — one rule or two, no tiebreaker |
| `no_group_sessions_on_fridays` | refuse — a type this practice does not have |

The last three are the point of the group. "I only do intakes on
Tuesdays" reads either as *intakes happen on Tuesdays and nowhere else*
(a narrowing) or as *Tuesdays are for intakes and nothing else* (a claim
on the window), and the two write different rules: one leaves Tuesday
afternoon open to every other kind of appointment and one closes it.
Nothing in the sentence decides between them, so the parser returns the
question rather than a rule. Same for a type the practice has never
configured — dropping the name would widen "no group sessions on
Fridays" into a Friday blocked for everything.

**Day-less hours and weekday caps** — a range of hours with no day
named, and a cap that holds on one weekday only. Both have a tempting
single answer that writes the wrong week.

| case | expects |
|---|---|
| `nine_to_five_no_days` | refuse, with two readings: 9–5 on weekdays, or 9–5 every day |
| `my_hours_are_ten_to_four` | the same, in other words and other hours |
| `nine_dash_five_no_fridays` | four `working_hours` rules, Monday to Thursday, and no Friday block |
| `max_two_on_saturdays` | refuse — `max_per_day` has no day, so it would cap every day |
| `up_to_two_anytime_saturday` | refuse — and never as 00:00–23:59 Saturday hours |

"9 to 5" with no day used to come back as one guessed day, which saves
one rule and leaves the other six days closed. Which days the hours cover
is the therapist's call, so the parser asks, and offers both answers.
Leaving a day out ("no Fridays") answers it: the hours are a working
week. Friday gets no block of its own, because a day with no working
hours already takes no appointments.

All nine rule types are covered many times over. Relative dates resolve
against a fixed anchor (`cases.REFERENCE_DATE`) so the corpus stays
deterministic.

**The out-of-scope and inversion cases are the load-bearing traps.**
Several wear the same "no … on `<day>`" or "`<day>` …" clothes as a real
day block while meaning something the engine cannot express, or meaning
the exact opposite of a block. A parser matching on a day name near a
negative-sounding word will produce a confident wrong rule on these,
which is exactly the failure this eval exists to price.

## The grading is asymmetric, and that is the point

| finding | consequence |
|---|---|
| a must-refuse sentence gets a rule | **hard failure** |
| a parseable sentence gets the wrong or an incomplete rule set | **hard failure** — a missing rule in a multi-rule sentence silently opens time meant to be blocked |
| right rules, wrong `enforcement` | soft finding, reported not gated |
| right rules, wrong `exclusive` flag | soft finding, reported not gated (see below) |
| a parseable sentence is refused | **always acceptable** — it falls through to the form. Reported as a recall miss, never gated |
| readings are offered, but not the ones the case pins | **hard failure** — the therapist saves the one they pick |
| the call fails after its retry | reported with the latencies as a failed call, never graded |

Refusing costs recall and nothing else. Guessing fails the run. A wrong
rule blocks or opens a calendar with nothing to tell the therapist it
happened; a refusal puts them in the form they were heading for anyway.

Exit code: `0` no hard failures, `1` hard failures, `2` a setup problem.

### The `exclusive` flag

The parser sets a top-level `exclusive` flag when a sentence states a
*complete* set of working hours ("I ONLY meet Mondays and Tuesdays"),
meaning the `working_hours` proposals in the response together are the
whole picture — every day not named is implicitly closed too, which none
of the eight rule types can encode directly (there's no "and nothing
else" rule). The parser already emitted this flag; the eval didn't grade
it until this expansion. `EvalCase.expected_exclusive` is `bool | None`
and defaults to `None` — unchecked — on every case that never turns
exclusivity on, so this doesn't retroactively assert `False` against 73
cases that never considered the question. Only `only_tue_thu_10_to_4_
exclusive` sets it, to `True`. A mismatch is graded exactly like an
`enforcement` mismatch: a soft finding, never gated — the schema gap that
makes exclusivity necessary in the first place is a real limitation, not
something a therapist should have a hard-failed rule proposal over.

### The 23:59 rest-of-day encoding convention

`block_time_range` has no "and everything after" shorthand — it takes a
literal `start`/`end`. Every "nothing after `<time>`" phrasing in this
corpus (`nothing_after_noon`, `nothing_after_17`,
`rather_not_book_after_six`, the second half of
`nothing_before_nine_or_after_five`, and the `block_time_range` half of
the compound cases) therefore expects `end: "23:59"` rather than
`"24:00"` — the engine's own time validator
(`AvailabilityRuleParseService._validate_time_range_params`) requires
`0 <= hour <= 23`, so `"24:00"` is not a value the parser could ever
legally emit, and would fail its own param validation before reaching
this eval at all. `"23:59"` is the practical maximum a real block can
reach, mirroring the corpus's existing `"00:00"` convention for the
symmetric "nothing before `<time>`" case.

### Design-pinned vs. unpinned ambiguity

Two sentences can look equally ambiguous in plain English and still get
different verdicts here, because the *system* — not the sentence — is
what decides whether there's one right answer.

`app/scheduling_engine/services/date_intent.py`'s weekday resolver pins
`"next <weekday>"` to *this week's occurrence plus a full 7 days*,
unconditionally — never the plain-English "nearest upcoming X" reading a
person would guess. Against this corpus's anchor (Tuesday 2026-08-04),
the plain reading of "next Friday" would be 2026-08-07 (3 days out); the
pinned resolver gives 2026-08-14. `out_next_friday` and
`out_this_and_next_friday` expect the **pinned** value, because the
system has already made this decision deterministically — an ambiguity
the design has resolved is no longer an ambiguity as far as the parser is
concerned, and a refusal here would be *wrong*, not merely conservative.

`block_out_friday_ambiguous` ("block out Friday") is the contrasting
case: nothing in `date_intent.py`, the system prompt, or anywhere else in
the codebase decides whether a bare "block out `<day>`" means a one-time
block or a standing `block_day_of_week` rule. No design decision exists
to pin it to, so it must refuse — and the live parser currently doesn't
(see "Hard failures" below).

### Soft-enforcement vocabulary

The system prompt's own instruction is narrow: default every rule's
`enforcement` to `"hard"`; use `"soft"` "only for explicit preference
language (\"I'd prefer not to...\")". This corpus's soft cases follow
that literally — "I'd rather not..." and "I'd prefer not to/to..." are
the only hedge phrasings graded as soft, applied to three different rule
types (`rather_not_book_after_six`, `prefer_not_more_than_five`,
`prefer_fridays_free_of_sessions`). `prefer_fridays_light` brackets the
convention from the other side: the hedge is there ("prefer to... if
possible") but there's no boundary underneath it for the hedge to
soften — "light" names no `max_per_day` count — so it refuses instead of
becoming a soft rule with an invented number. **The rule of thumb: a
hedge over a complete, concrete boundary parses soft; a hedge with no
boundary underneath it refuses.**

## Latency and pass rate — recorded 2026-10-02

Production parses had gone from 3-5 s to 20-35 s, and one run against
dev took 168 s, with nothing logged to show it. Full runs of the 89-case
corpus, `gemini-3.5-flash` on the Vertex global endpoint, from a laptop:

| | recall | correct refusals | hard failures | soft findings | failed calls | p50 | p95 | max |
|---|---|---|---|---|---|---|---|---|
| before, run 1 | 43/47 | 37/42 | 7 | 1 | 0 | 1542 ms | 7511 ms | 23018 ms |
| before, run 2 | 45/47 | 37/42 | 6 | 1 | 0 | 1566 ms | 3874 ms | 16196 ms |
| after, run 1 | 44/46 | 40/42 | 2 | 1 | 1 | 1575 ms | 6207 ms | 20744 ms |
| after, run 2 | 45/47 | 40/42 | 2 | 1 | 0 | 1678 ms | 6093 ms | 11901 ms |

"Before" is the parser as it stood, graded against today's corpus: the
five new cases account for four of its hard failures and its soft
finding, and the rest are `only_until_noon_wednesdays`,
`block_out_friday_ambiguous` and (once) `half_hour_between_clients`.
"After" fails only the first two, which have failed since the 2026-08-30
baseline below. Its one failed call was a 429 from the project's quota,
not a timeout. The p95 after reflects the new readings cases: two
readings of a whole week is a dozen proposals, 6-8 s of output, where
the old parser answered sooner with a guess.

What the time went into, measured before deciding anything:

- **The long tail was the service, not the schema.** Repeating six
  sentences three times under each response shape turned up a 94 s call
  that produced 112 output tokens with the old nested schema, and a 26 s
  call that produced 80 with no readings in the schema at all. A stalled
  call was bounded only by the client's 180 s read timeout. Each attempt
  is now bounded at 15 s and retried once, the retry cut to what is left
  of the 25 s request deadline, so no parse waits longer than 25 s.
- **That bound has to stay client-side.** Given as the server's deadline
  (the SDK's default when a request sets a timeout), Vertex answered 504
  DEADLINE_EXCEEDED well short of it: at 10 s of a 15 s deadline, and on
  2 of 15 runs of "9 to 5" that otherwise take 6-8 s. With the bound
  client-side and the server's deadline left at 180 s, the same 45
  calls had no errors.
- **Readings cost their output tokens and little else, so the schema
  stays as it was.** The nested `readings` array, flat
  `reading_a`/`reading_b` siblings and no readings at all came within a
  few hundred milliseconds of each other on ordinary sentences. Five
  runs each of three sentences a therapist typed into production
  ("9-5, no Fridays, and a max of 2 intakes on Tuesdays", "I can see up
  to 2 patients anytime on Saturday", "No meetings on Wednesday") took
  a median of 3.8 / 1.9 / 1.6 s with the nested schema and 3.3 / 1.6 /
  1.5 s with none, and produced no readings either way. Where readings
  are produced, time tracks output: 1,760 tokens took 6-8 s. A flat
  variant was tried and measured no faster, so it was dropped.
- **The prompt says more than it did.** Partway through, both a flat
  schema and one with no readings dropped `buffer_before` from "N
  minutes between clients", and the flat one parsed "I hate Mondays" as
  a block. The prompt now says that a gap between appointments is a
  buffer on both sides, and that a feeling about a day is not an
  instruction.
- **Production's 24-36 s was not this code, this input or this
  model.** The four slow production calls (2026-10-02 22:22-22:45Z)
  each made one request to
  `locations/global/publishers/google/models/gemini-3.5-flash`, the
  same model and endpoint dev calls, and two of them returned a single
  short proposal. The same code on dev, with the same sentences, takes
  1.6-3.8 s. Production had taken 3.4-5.1 s with the same configuration the
  week before. What the slow window shares is Vertex itself: stalls of
  15-90 s turned up from a laptop too, in bursts, and a later run
  against dev went from no timed-out calls to several within the hour.

The parse result log line now carries `latency_ms` and `output_tokens`
beside the rule types and refusal reason, so the next regression shows
up in the logs rather than in a therapist's wait.

### Model comparison — recorded 2026-10-03

The same prompt and schema on four models, two full runs each with the
15 s per-attempt bound (2026-10-02 23:50Z to 2026-10-03 00:40Z), plus one
run of three of them with the bound lifted, to read quality free of
timeouts. Every model accepted `thinking_budget=0` and the response
schema.

| model | hard failures | recall | failed calls | p50 | p95 | max |
|---|---|---|---|---|---|---|
| gemini-3.5-flash | 2, 2 (2 unbounded) | 44/47, 44/47 | 2, 1 | 1.7 s | 8.0-16.8 s | 30 s |
| gemini-3.5-flash-lite | 3, 3 | 36/46, 39/47 | 1, 0 | 1.2 s | 3.4-4.8 s | 18-31 s |
| gemini-3.8-flash | 0, 0 (1 unbounded) | 41/45, 36/39 | 7, 19 | 6.2-7.8 s | 30 s | 31 s |
| gemini-3.1-pro-preview | 1, 1 (0 unbounded) | 43/45, 45/46 | 5, 3 | 4.6-4.9 s | 19.7-30.3 s | 31 s |

A failed call is graded as nothing, so a model that times out on a hard
case scores better than it should: 3.8-flash's two zeros include runs
where `block_out_friday_ambiguous` timed out, and with the bound lifted
it failed that case like the others. Read the hard failures that way.

- 3.5-flash-lite is the fastest and the least accurate: it adds a hard
  failure (`mornings_only_tuesdays`) and refuses more parseable cases.
- 3.8-flash and 3.1-pro pass `only_until_noon_wednesdays`, which
  3.5-flash fails, and pro sometimes passes `block_out_friday_ambiguous`.
  Both take 2-4x as long per parse, and most of their failed calls are
  the 15 s bound cutting off answers that would have arrived.
- On the three production sentences, five runs each: 3.5-flash 1.6 /
  1.6 / 2.0 s median (one 17 s stall), 3.8-flash 2.6 / 2.2 / 2.6 s,
  flash-lite 1.1 / 1.2 / 1.2 s, pro 6.0 / 3.9 / 3.5 s.

3.5-flash stays: one hard case is not worth tripling the wait, and both
cases it misses are prompt work, not model work.

## Hard failures — recorded baseline

Three consecutive runs against the live parser, 2026-08-30, each a full
pass over all 74 cases at the time:

```
run 1:  recall 40/41  correct refusals 30/33  hard failures 3  soft findings 0
run 2:  recall 39/41  correct refusals 30/33  hard failures 3  soft findings 0
run 3:  recall 39/41  correct refusals 30/33  hard failures 3  soft findings 0
```

Recall varies slightly run to run — this is a real model call per case —
but all three hard failures are now **stable across all three runs**:

- `block_out_friday_ambiguous` — "block out Friday" produces
  `block_day_of_week` every time. The parser doesn't yet distinguish a
  one-time block from a standing rule; it defaults to standing. This is
  the "design-pinned vs. unpinned" trap working as intended (see above) —
  a real, reproducible gap, not corpus noise.
- `i_hate_mondays_sentiment` — "I hate Mondays" also produces
  `block_day_of_week` every time. The parser matches the day name near
  negative sentiment rather than recognizing there's no instruction here
  at all. Notably, the sibling inversion case
  `fridays_work_great_inversion_trap` ("Fridays work great for me")
  refused cleanly in all three runs — so the model does catch positive
  statements, just not bare negative sentiment with no rule content.
- `only_until_noon_wednesdays` — "I only see clients until noon on
  Wednesdays" produces `working_hours` every time (an implied start of
  `"08:00"` in two runs, `"00:00"` in one — the model isn't even
  internally consistent about which lower bound to invent). This case
  was originally drafted as parseable with an implicit `00:00` start;
  arbitration reclassified it to must-refuse before this baseline was
  recorded, for the same reason `mornings_only_tuesdays` already
  refuses: the sentence gives an upper bound only, `block_time_range`
  has no `day_of_week` field so `working_hours` is the only rule type
  that *could* express a day-scoped cutoff, and `working_hours` requires
  a start the sentence never states. Inventing one is the harmful
  direction, not the safe one — a guessed `00:00` doesn't just fail to
  block time, it *opens* midnight-to-8am Wednesday availability the
  therapist never offered. **This reclassification makes the recorded
  numbers in this baseline strictly harsher than they would otherwise
  have been** — the three runs immediately above already reflect it (all
  three now count this case as a hard failure; a version of this corpus
  that kept it positive recorded only 2-3 hard failures across the same
  three runs, with this one flaking between pass and fail).

`no_sessions_week_of_20th` and `nothing_before_nine_or_after_five` (a
recall miss in runs 2 and 3) account for the recall variance — both are
refusals-of-a-parseable-sentence, which cost nothing and are never
gated.

**This is an expansion baseline, not a new pass bar.** The prior
14-case corpus recorded zero hard failures; this 74-case corpus records
three, stably, across all three recorded runs. That is the corpus doing
its job — it now measures three real gaps the smaller corpus had no
case shaped to catch. Closing them is parser follow-up work, not a
blocker on this PR, and no case here was written or adjusted to make
the current parser pass — if anything, the one reclassification made
during this baseline's own drafting made the numbers worse, not better.

## On this expansion

The corpus grew from 14 to 74 cases across two independently-drafted
passes (drafted blind, without running either against the live parser),
arbitrated case-by-case for near-duplicates, corrected expectations, and
category assignment. A few notes on that process:

- **`no_sessions_week_of_20th`'s pin was inherited, not re-litigated.**
  The original 14-case port already resolves "the week of the 20th" to
  the calendar week (Mon–Sun) containing it, 2026-08-17 through 08-23 —
  the correct pin — so no change was needed here.
- **`date_token_gap` is a new category**, used only for
  `date_token_gap_named_holidays` and `off_for_thanksgiving_week`. It's
  distinct from `ambiguous` (there IS exactly one correct calendar
  answer for "Christmas") and from `out_of_scope` (`block_date_range` is
  the right rule type). The gap is that the parser's system prompt
  explicitly forbids resolving a holiday name from world knowledge — it
  must reduce every date reference to an explicit `MM-DD`/`YYYY-MM-DD`
  token or a weekday+modifier token, and a holiday name is neither, so it
  must refuse. It doesn't correspond to a `refusal_reason` value the
  parser itself emits (those are `ambiguous`/`out_of_scope`/
  `multi_intent`, unchanged) — this eval's `category` field is a
  reporting label the corpus uses to group cases, not a value graded
  against the parser's own output.

### Considered and rejected

Both drafting passes proposed cases that didn't make it in. Recorded
here so the boundary is documented rather than lost:

- **Bare "I'm out Monday"** — genuinely ambiguous between "I'm out this
  coming Monday" (one date) and "I'm out on Mondays" (a standing rule) —
  two different rule *types*, not just two readings of the same one, and
  neither sentence-internal cue picks a side.
- **"Keep my mornings free"** — the same unbounded-"morning" shape as
  `mornings_only_tuesdays`, with no day named either; would have
  duplicated an existing trap rather than adding a new dimension.
- **A just-passed explicit date ("August 1st" against the anchor)** — cut
  because it wouldn't actually test what it looked like it tested: a
  year-less `MM-DD` token rolls forward automatically when passed (the
  same mechanism `out_july_4th_rolls_year` already exercises), so this
  would have been a near-duplicate rather than a new case.
- **"Emergency-only Saturdays"** (or similar "special-case clients only")
  — the same which-clients-qualify shape as `out_of_scope_insurance_
  mondays`; adds a day name but not a new failure mode.
- **Bare "9 to 5"** — cut from this expansion as adjacent to
  `done_by_three` and `nine_to_five_mon_thu`. It came back later as
  `nine_to_five_no_days`, after a therapist's first-run calendar showed
  what the parser did with it: one guessed day.
- **Inventing a number to rescue a hedge into a positive case** (e.g.
  giving `prefer_fridays_light`'s "light" a made-up `max_per_day` count)
  — rejected outright as the exact anti-pattern this eval polices; kept
  as the refusal it is instead.
- **An out-of-scope case sharing no rule-type vocabulary at all** (e.g. a
  no-show/cancellation-fee policy sentence) — cut because it isn't
  actually a *trap*: nothing about it would tempt a parser into firing.
  Every out-of-scope case kept here deliberately borrows real rule-type
  vocabulary, which is what makes it load-bearing.
- **A fourth rule type folded into the three-rule compound sentence** —
  cut for readability; a four-clause run-on stopped reading like
  something a therapist would actually type, and three distinct types
  already covers the "not just repeated same-type rules" gap the
  original `nine_to_five_mon_thu` left.

## On the corpus itself

Every phrasing here is a therapist describing their own calendar, so the
corpus carries no patient data of any kind — see this directory's parent
`README.md` for the rule that governs eval data generally. The eval is
self-contained: a corpus, a scorer and a runner in this package, with no
hosted service behind it.

## Running it

```bash
export GOOGLE_CLOUD_PROJECT=pablohealth-dev
gcloud auth application-default login       # once

scripts/run-availability-parse-eval.sh              # the whole corpus
scripts/run-availability-parse-eval.sh --list       # the cases, no model calls
scripts/run-availability-parse-eval.sh --case friday
scripts/run-availability-parse-eval.sh --json
```

Every case is a real model call, so a full run over all 89 cases takes
roughly three minutes and costs what eighty-nine flash-tier calls cost.
`--list` needs neither credentials nor a project.
