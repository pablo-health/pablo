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
| `the_stated_action` | a medication proposal is a different action (a change where the clinician stopped it) | accepting does what the visit decided |
| `cites_the_lines_that_say_it` | the proposal cites none of the lines that state the change, or, from a document, leaves out the paragraph that disagrees | the evidence the clinician sees is the sentence that says it, and a conflict in the document is shown |
| `says_nothing_it_never_should` | a free-text proposal carries what belongs elsewhere, such as a medication in a history field | the medication list stays the place medications are kept |

A proposal is named by its field and, for a list field, its entry: a
medication's proposals are `medications: <name>`, one per medication.

The checks are unit-tested on hand-made proposals in
`backend/tests/test_chart_proposal_eval_scorers.py`.

## Cases

| Case | Chart | Visit | Expected |
|---|---|---|---|
| `divorce-finalized` | separated, divorce in progress; custody shared | the divorce was finalized; everything else restated | `relationships` and `legal_custody`, each keeping its text and adding that the divorce was finalized, citing the client's lines |
| `unchanged` | the same chart | every field restated, nothing new | no proposal |
| `stopped-working` | full time as a dental hygienist | stopped working there at the end of August | `work_school` still naming the dental practice and saying it no longer applies |
| `medication-start-and-stop` | sertraline, trazodone | the clinician starts hydroxyzine 25 mg in the afternoon as needed and stops the trazodone because of nausea | a start with its frequency and a stop with its reason; nothing for the sertraline, continued |
| `medication-only-discussed` | sertraline | a medication asked about, a dose increase considered for next time | no proposal |
| `medication-another-prescriber-started` | sertraline | the client's primary care doctor started lisinopril 10 mg once a day | an add with the dose and frequency as stated |
| `medication-client-stopped` | sertraline, buspirone | the client stopped the buspirone; the clinician decides nothing yet | no proposal |

The history cases' charts list the medication the client mentions taking, as
a follow-up's chart would; without it, that mention is a medication the list
lacks and is rightly proposed as an add.

| `transfer-note` | empty | an imported follow-up note from another records system (`backend/tests/fixtures/notes/transfer_psychiatric_follow_up.txt`) | `alcohol`, `tobacco_nicotine`, `work_school` and the penicillin allergy, each citing its paragraph; the sertraline and hydroxyzine the plan continues as adds; `supports` and `relationships` may be proposed or not; no medication in a history field |
| `carried-block-is-stale` | full time as a dental hygienist; sertraline 50 mg | an imported note whose carried social history says the client works and whose interval history says they were laid off; its carried medication list (sertraline 50 mg, trazodone) disagrees with its plan (sertraline 100 mg, start buspirone) | `work_school` following the interval history and citing the carried paragraph too; the sertraline change to 100 mg citing the plan and the carried list; the buspirone start citing the plan; nothing for the trazodone only the carried list names; no medication in a history field |

These two read the document a paragraph at a time, as an imported note's
proposals do.

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

### Imported notes and the medication list

Configured note model, against a development project, three runs of each of
the nine cases: 27 of 27 passed, and six further runs of
`carried-block-is-stale` passed.

Before the document's prompt said anything about medications, every run of
`carried-block-is-stale` added the trazodone that only the carried list
names; the medication list's own rules, written for a visit, read a listed
medication the chart lacks as one the client takes. The document's prompt
now says its plan is the clinician's decision, that a medication the plan
continues and the list lacks is an add, and that one only a carried block
names needs nothing. With that added, the stale-block case cited only the
interval history for `work_school` in four runs of six; the prompt now says
the rule about citing the disagreeing paragraph holds for every field.

### With the medication list

Configured note model, against a development project, three runs of each of
the seven cases: 21 of 21 passed. Every run proposed the hydroxyzine start as
`hydroxyzine 25 mg, in the afternoon as needed`, the trazodone stop as
`Stopped: nausea` and the lisinopril add as `lisinopril 10 mg, once a day in
the morning`, and nothing for the discussed medication or the client's own
stop.

### History fields

Configured note model, against a development project, three runs of each
case: 9 of 9 passed.

The first run, before the prompt said where current medications belong,
proposed a `medication_trials` entry from the medication the client takes
now in both the divorce and the stopped-working visits. The prompt now says
a current medication, or one started, stopped or changed this visit, is
never proposed to a history field.

With the two imported-note cases added, three runs of each of the five
cases: 15 of 15 passed, and five further runs of `carried-block-is-stale`
passed. Two earlier runs failed and changed the prompt. The stale-block
case proposed the assessment's diagnosis to `prior_diagnoses`; the prompt
now says an assessment's diagnoses reach the problem list from the note.
One run in three cited only the interval history; the prompt now says a
proposal following one part over another cites both.
