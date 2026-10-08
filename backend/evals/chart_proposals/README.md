# Chart-proposal eval

After a note is drafted, a second, smaller call compares the client's chart
with the visit's transcript and proposes an update for each chart field the
visit changed (`app/chart_proposals/drafting.py`). The clinician accepts,
edits or discards each one when signing. This eval runs that call with the
real model on synthetic visits and grades the proposals with deterministic
checks. There is no model judge.

The proposals graded are the ones that survive the server-side checks: a
proposal citing no transcript line, or a line the visit does not have, is
already gone.

## What it grades

Any problem from any check fails the case.

| Check | Fails when | Protects |
|---|---|---|
| `exactly_the_expected_fields` | a field the visit changed has no proposal; a field nothing changed has one; a field has two | review costs nothing on an ordinary visit, and a change stated once reaches every field it changes |
| `text_kept_and_changed` | the proposed text leaves out what the chart said, or does not say what changed | a proposal amends and appends, never removes: what stopped being true is kept and said to no longer apply |
| `cites_the_lines_that_say_it` | the proposal cites none of the lines that state the change | the evidence the clinician sees is the sentence that says it |
| `no_gendered_pronouns` | a proposal calls the client he or she (no case's chart records pronouns) | the chart never assumes a client's gender |

The checks are unit-tested on hand-made proposals in
`backend/tests/test_chart_proposal_eval_scorers.py`.

## Cases

| Case | Chart | Visit | Expected |
|---|---|---|---|
| `divorce-finalized` | separated, divorce in progress; custody shared | the divorce was finalized; everything else restated | `relationships` and `legal_custody`, each keeping its text and adding that the divorce was finalized, citing the client's lines |
| `unchanged` | the same chart | every field restated, nothing new | no proposal |
| `stopped-working` | full time as a dental hygienist | stopped working there at the end of August | `work_school` still naming the dental practice and saying it no longer applies |

## Running it

```bash
export GOOGLE_CLOUD_PROJECT=<a project with Vertex access>
export GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
gcloud auth application-default login       # once

scripts/run-chart-proposal-eval.sh              # every case once
scripts/run-chart-proposal-eval.sh --runs 3     # every case three times
scripts/run-chart-proposal-eval.sh --case unchanged
```

## Recorded runs — 2026-10-08

### Pronouns

Before the prompt said how to refer to the client, every run of
`stopped-working` wrote "when she was let go". With the rule, three runs
of it passed with no gendered pronoun ("they were let go", or no pronoun at
all), and a run of every case passed.

Configured note model, against a development project, three runs of each
case: 9 of 9 passed.

The first run, before the prompt said where current medications belong,
proposed a `medication_trials` entry from the medication the client takes
now in both the divorce and the stopped-working visits. The prompt now says
a current medication, or one started, stopped or changed this visit, is
never proposed to a history field.
