# Starting-template eval

Settings offers starting templates for note types: the psychiatric
follow-up and the psychiatric initial evaluation, in
`frontend/src/components/settings/noteTypes/templates/`. Each comes with
sample visits a clinician can draft under "Try it". This eval drafts those
samples with the real model, through the same path a preview takes, along
with visits written for the eval to test the chart rules. A set of
deterministic checks grades every draft. There is no model judge: each
check reads the draft and fails on what it says.

The templates' rules exist because the draft is the start of a coded
progress note. A code, a time or a monitoring-program check the clinician
did not dictate, or a risk level the clinician did not state, is a claim in
the record that nobody made. The chart is the record of what is true about
the client, so a field the template fills from the chart says what the
chart says. Anything the visit adds goes after the chart's text, marked
"(stated this visit)", so the clinician can review it at signing. The
checks below make those rules hard.

## What it grades

Any problem from any check fails the case. A check reads only the fields
the case's template has: the evaluation has no self-harm field, and its PDMP
line sits under Prescriptions rather than Plan.

| Check | Fails when | Protects |
|---|---|---|
| `codes_only_dictated` | anywhere in the note, a procedure code (9xxxx, Gxxxx) the clinician did not dictate; a clock time not dictated in the visit details, the psychotherapy time, or any field that ties it to the session (start, end, began, session, visit from) — a time the client mentions, like a medication wearing off "by 9:00 AM", is the client's words, not a claim about the visit; minutes in the psychotherapy time that were not dictated; any psychotherapy time or minutes restated in the visit details; a dictated code, time or minutes count missing from where the template puts it | codes and psychotherapy time come only from the clinician's words, verbatim, never estimated from timestamps, and the psychotherapy time is stated once, so a confirmed window leaves no stale copy |
| `psychotherapy_section` | any psychotherapy field written for a visit with no therapy ("Not stated." included); the issues or interventions empty for a visit with therapy | a medication-only visit never reads as one with a therapy portion |
| `risk_quoted` | suicidal/homicidal ideation, self-harm/violence or overall acute risk is neither a quotation nor "Not stated."; a quotation that is not in the transcript; a risk level (low, moderate, high, ...) written outside a quotation; where the clinician stated the finding, the field is "Not stated." or quotes something else | the draft records what was said and the clinician's own judgment, never its own |
| `safety_plan` | a safety plan written when no ideation, self-harm or violence was reported; none written when ideation was reported and the clinician described one | no safety planning appears that did not happen, and none that did is lost |
| `pdmp_line` | when the check was dictated: the line says "today", carries no calendar date, carries a date other than the date of service, or leaves out the dictated finding; when it was not: the line claims a check | a monitoring-program review is dated, and never claimed when not dictated |
| `telehealth_attestation` | a telehealth visit's attestation leaves out "telehealth" or either entered location; an office visit is not "in-office" or mentions telehealth | the place-of-service attestation names both locations as entered |
| `substances` | a substance never asked about does not read "Not asked"; one asked about has no answer; a screen field is the chart's baseline, word for word | a question never asked is not recorded as a denial, and last visit's answer is not passed off as this one's |
| `diagnoses_only_stated` | the diagnosis list is empty when something was entered or named, or names a diagnosis neither on the chart's problem list nor named by the clinician; a coded diagnosis named without its code; anywhere in the note, a diagnosis code from neither | no diagnosis or code is invented, and none given is lost |
| `measures_undated` | a measures field carries a calendar date (the visit says "on Friday") | a weekday is never converted into a date |
| `medications_from_chart` | the current medications leave out a line the chart has, word for word ("None recorded" when it has none), or add an unmarked one it does not; a change made in this visit appears in the current list, or is missing from the plan | the current list is the chart's, and a start, stop or change is in the plan |
| `intake_states_meds` | a medication the client said they take, and the chart lacks, is missing from the current list or listed without the mark | a client's own list is never dropped on an empty chart, nor passed off as the chart's |
| `allergies_never_dropped` | the allergies field leaves out the chart's value (each recorded allergy, NKDA, or "Not recorded"), or what was said about allergies this visit | a stated allergy is added, never swapped in; a disputed allergy stays until someone removes it from the chart |
| `suffix_only_where_stated` | a chart-fed field (each history field the template fills from the chart, the current medications, the allergies) is marked "(stated this visit" when the case says nothing new was stated about it, or unmarked when it was | the mark is how the clinician finds what to update at signing: missing, a change goes unnoticed; stray, it asks for review of nothing |
| `history_from_chart` | a history field the template fills from the chart does not start with the chart's text word for word, or with "Not recorded" when the chart has nothing, or carries anything after it that is not marked | history comes from the chart as recorded; what the visit adds follows it, marked |
| `history_from_visit` | in a template that takes history from the visit (the evaluation), a field the visit covered is empty, "Not recorded" or "Not stated.", or leaves out what was said | an intake writes the history it took |

The checks are unit-tested on hand-made drafts, passing and failing, in
`backend/tests/test_note_template_eval_scorers.py`.

## Cases

The first two transcripts are the follow-up template's own samples. The
others are in `visits.py`, written for the eval in the same format. The
values entered before the visit and the chart behind it are in `cases.py`.

| Case | Visit | Chart and entries | The draft must carry |
|---|---|---|---|
| `follow-up-with-therapy` | sample `with_therapy` | telehealth, both locations; two coded problems, two psychiatric medications, three history fields, an alcohol baseline | the three history fields word for word and every other one "Not recorded"; the chart's medications as listed, with sertraline still at 50 mg and the increase to 75 in the plan; 99214 and 90836, 10:14 to 10:55 and 41 minutes, the PDMP check dated 2026-03-12 (the clinician said "today") with its finding, both locations, a psychotherapy section |
| `follow-up-medication-only` | sample `medication_only` | in office; one uncategorized medication; nothing else | every history field "Not recorded"; the chart's bupropion line as listed; no code, time, minutes or PDMP claim; an empty psychotherapy section; "Not asked" for tobacco and cannabis; only the depression the clinician named, with no code |
| `follow-up-full-chart` | stable, nothing changes | in office; two coded problems, a sulfa allergy, psychiatric and other medications with frequency, every history field, a four-substance baseline | every chart-fed field word for word with no mark anywhere; "asked — no change" (or the answer) for alcohol, cannabis and nicotine, never the baseline copied; "Not asked" for the five not named |
| `follow-up-stated-change` | laid off since the last visit; a medication from another doctor; bupropion started | telehealth; one coded problem, NKDA, one medication, four history fields | the chart's work history, then the layoff marked; escitalopram as listed, then omeprazole marked; bupropion in the plan and not in the current list; 99214; nothing else marked |
| `follow-up-allergy-stated-on-nkda` | the client reports an amoxicillin rash | in office; bipolar II, NKDA, lamotrigine | NKDA, then amoxicillin, marked |
| `follow-up-allergy-disputed` | the client says the penicillin allergy was a sibling's | telehealth; ADHD, penicillin (hives), atomoxetine | penicillin still there; the dispute may be quoted after it, marked |
| `follow-up-empty-chart` | sleep only; no diagnosis named | in office; nothing on the chart | every history field and the allergies "Not recorded", the medications "None recorded", no mark; no diagnosis; "Not asked" for every substance but alcohol and cannabis |
| `evaluation-empty-chart` | an intake: the client lists two medications and denies allergies; the clinician states two coded diagnoses | telehealth, 90792; nothing on the chart | each history field from what the client said; "None recorded", then levothyroxine and omeprazole, marked; escitalopram in the plan only; allergies "Not recorded (stated this visit: …)" with the denial quoted; F41.0 and F41.1 and no other diagnosis; the dictated 2:00 to 2:55 and 90792 and no other time or code |
| `follow-up-risk-language` | passive suicidal ideation; the clinician states the level and the safety plan in the dictation | telehealth; one coded problem, NKDA, sertraline 150 | ideation quoted; overall risk the clinician's "moderate", quoted, never outside a quotation; the safety plan written; 200 mg in the plan, 150 in the current list |

In the medication-only sample the clinician asks "Any alcohol or anything
else?" and the client answers only about alcohol. Other substances are
left ungraded: asked, but not answered. In the therapy sample the
clinician dictates "supportive partner", so a marked relationships field is
allowed but not required.

## Running it

```bash
export GOOGLE_CLOUD_PROJECT=<a project with Vertex access>
export GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
gcloud auth application-default login       # once

scripts/run-note-template-eval.sh                      # every case once
scripts/run-note-template-eval.sh --runs 3             # every case three times
scripts/run-note-template-eval.sh --case medication-only
scripts/run-note-template-eval.sh --out /tmp/template-eval   # drafts in results.json
scripts/run-note-template-eval.sh --list               # the cases, no model calls
```

A draft takes 15 to 50 seconds. The model is the configured note model,
and `--model` overrides it. With `BRAINTRUST_API_KEY` set,
`backend/evals/test_note_templates.py` pushes the cases to the
`starting-templates` dataset in `pablo-note-generation`.

## Recorded runs — 2026-10-08

Configured note model, against a development project, the templates as on
`main` before the "(stated this visit: …)" mark was extended to history
fields. Four runs of every case.

| Case | Passed | What failed |
|---|---|---|
| `follow-up-with-therapy` | 0 of 4 | the self-harm/violence field paraphrased (4 of 4); "Not asked" for other substances after "Nicotine, cannabis, anything else?" / "No, none of that." (2 of 4) |
| `follow-up-medication-only` | 3 of 4 | the self-harm/violence field paraphrased |
| `follow-up-full-chart` | 0 of 4 | "wears off by nine" written as "9:00 AM" (4 of 4); the self-harm/violence field paraphrased (2 of 4) |
| `follow-up-stated-change` | 0 of 4 | the work history left as the chart's text with nothing marked; the layoff only in the interval history (4 of 4) |
| `follow-up-allergy-stated-on-nkda` | 4 of 4 | |
| `follow-up-allergy-disputed` | 4 of 4 | |
| `follow-up-empty-chart` | 4 of 4 | |
| `evaluation-empty-chart` | 4 of 4 | |
| `follow-up-risk-language` | 4 of 4 | |

The first run of the two allergy cases wrote a medication with its
category heading on the same line ("Psychiatric: Lamotrigine 100 mg, twice
daily"). The chart's line is all there, so `medications_from_chart` now
reads it as the chart's line. Those first runs are counted above as
passing.

The paraphrased self-harm field is the failure recorded on 2026-10-06, and
it still needs a template change. The unmarked work history is what the
history-field mark is for, so that case should pass once it lands.

## Recorded runs — 2026-10-06

Configured note model (`ai_model`, `gemini-3.1-pro-preview`), against a
development project. Four runs of each case (one, then three in a row).

- Every check but `risk_quoted` passed in every draft.
- `risk_quoted` failed in 5 of 8 drafts, always on the self-harm/violence
  field: a paraphrased denial ("The client denies any thoughts of hurting
  themselves or anyone else.") instead of a quotation. Medication-only:
  4 of 4. With therapy: 1 of 4. The ideation field beside it quoted every
  time. The check stays hard; the template's hint for that field is what
  needs to change.

Seen, not graded:

- In the medication-only draft, "Medication adherence." was written as a
  protective factor every time; neither the client nor the clinician named
  one.
- In two medication-only drafts the ideation field quoted the clinician's
  question ("thoughts of hurting yourself or anyone else") as what the
  client denied. The words are in the transcript, so the check passes; it
  cannot tell who said them.
