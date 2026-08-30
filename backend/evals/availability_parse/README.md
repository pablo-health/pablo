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

All eight rule types are covered. Relative dates resolve against a fixed
anchor (`cases.REFERENCE_DATE`) so the corpus stays deterministic.

**The two out-of-scope cases are the load-bearing trap.** Both wear the
same "no … on `<day>`" clothes as a real day block while meaning something
the engine cannot express — who may book, not when slots exist. A parser
matching on a day name near the word "no" will produce a confident wrong
rule here, which is exactly the failure this eval exists to price.

## The grading is asymmetric, and that is the point

| finding | consequence |
|---|---|
| a must-refuse sentence gets a rule | **hard failure** |
| a parseable sentence gets the wrong or an incomplete rule set | **hard failure** — a missing rule in a multi-rule sentence silently opens time meant to be blocked |
| right rules, wrong `enforcement` | soft finding, reported not gated |
| a parseable sentence is refused | **always acceptable** — it falls through to the form. Reported as a recall miss, never gated |

Refusing costs recall and nothing else. Guessing fails the run. A wrong
rule blocks or opens a calendar with nothing to tell the therapist it
happened; a refusal puts them in the form they were heading for anyway.

Exit code: `0` no hard failures, `1` hard failures, `2` a setup problem.

## Recorded result

Three consecutive runs on 2026-08-30, identical each time:

```
  availability-parse eval
  recall (exact match on parseable cases) . 7/8
      refused (acceptable, not gated): no_sessions_week_of_20th
  correct refusals (must-refuse cases) .... 6/6
  hard failures ............................ 0
  soft findings ............................ 0
  OVERALL .................................. PASS
```

Zero hard failures on all fourteen, including both out-of-scope traps and
both multi-intent sentences.

The single miss is a refusal, not an error: "the week of the 20th" gives
the parser a day with no month, and its date vocabulary carries an
explicit date or a weekday, neither of which that phrase is. It says so
and falls through. That is the conservative date path working as designed
— it never computes a date itself, so it cannot quietly compute a wrong
one — and the cost is one recall point rather than a mis-blocked week.

**The bar for a change here: zero hard failures, and recall no lower than
7/8.** Matching the recall while mis-firing on a trap is a regression, not
an improvement.

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

Every case is a real model call, so a full run takes roughly 25 seconds
and costs what fourteen flash-tier calls cost. `--list` needs neither
credentials nor a project.
