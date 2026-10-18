# Homework read eval

A psychiatric note's plan is prose that mixes what the prescriber will do
(medication changes, labs, follow-up timing) with, sometimes, what the
client agreed to do before the next visit. `app.homework.drafting` reads
those fields and proposes the second kind as homework. Every proposal still
has to be confirmed by the clinician; nothing reaches a client on its own.

So the interesting question is how often the read is *confidently wrong*:
a medication or crisis step offered as homework, or a task on a plan that
has none. This corpus puts a number on that.

## What it grades

`cases.py` holds 20 synthetic plans, written from scratch (no real note,
transcript or person is behind any of them), in three groups:

- **mixed** (10): homework among medication, lab and visit instructions.
- **none** (5): plans with no homework; the right answer is an empty list.
- **trap** (5): wording that invites a mistake — a taper schedule phrased as
  "client will take", crisis steps, home blood-pressure checks, a task
  completed last time, booking appointments.

Each case lists the tasks that must be found (every listed word must appear
in one kept task) and words no kept task may contain.

- A kept task containing a forbidden word is a **hard failure**.
- Any task on a plan with none is a **hard failure**.
- Recall across the corpus is gated at **0.85**: a miss means the clinician
  adds the task by hand.
- `ungrounded` counts tasks the model returned but could not quote from the
  note. They are dropped before anyone sees them, so they are reported, not
  gated.

## Running it

```bash
# On the note model this deployment is configured with (note_generation):
GOOGLE_CLOUD_PROJECT=<project> scripts/run-homework-proposals-eval.sh

# One Claude model alone, on the Anthropic API (ANTHROPIC_API_KEY):
scripts/run-homework-proposals-eval.sh --model anthropic-api:<claude model id>

# One model alone, on Amazon Bedrock:
scripts/run-homework-proposals-eval.sh --model bedrock:<model or inference profile id>

scripts/run-homework-proposals-eval.sh --case trap --json
```

Exit code 0 when the gates hold, 1 when they do not, 2 on a setup problem.
