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

The corpus is 74 cases: the original 14 plus a 60-case expansion drafted
in two independent passes and merged by arbitration (see "On this
expansion" below). 41 are parseable, 33 must refuse (55.4% / 44.6%).

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

All eight rule types are covered many times over. Relative dates resolve
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

## Hard failures — recorded baseline

Three consecutive runs against the live parser, 2026-08-30, each a full
pass over all 74 cases:

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
- **Bare "9 to 5"** — no day named at all, genuinely ambiguous about
  scope (every day? weekdays only?) — cut as adjacent to `done_by_three`
  (no-day-named `block_time_range`) and `nine_to_five_mon_thu`
  (day-ranged `working_hours`) without adding a dimension neither of
  those already covers.
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

Every case is a real model call, so a full run over all 74 cases takes
roughly two minutes and costs what seventy-four flash-tier calls cost.
`--list` needs neither credentials nor a project.
