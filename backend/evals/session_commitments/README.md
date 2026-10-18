# Session-commitment eval

After a session's note is drafted, one small call reads the transcript and
lists what the clinician said they themself would do after the session ("I'll
send the referral by Friday"), each one proposed as a task tied to the client
(`app/chart_proposals/commitments.py`). The clinician edits, accepts or
dismisses each one; nothing is filed until they accept. This eval runs that
call with the real model on synthetic sessions and grades what survives the
server-side checks. There is no model judge.

## What it grades

Any problem from any check fails the case.

| Check | Fails when | Protects |
|---|---|---|
| `recall` | something the clinician said they would do has no proposal | a commitment made out loud is not forgotten |
| `nothing_invented` | a proposal matches nothing the clinician said they would do | the list costs nothing to review on a session with no commitments |
| `client_work_kept_out` | a proposal names the client's own work, or another person's | homework stays the client's; the clinician's list holds only what they will do |
| `dates_only_when_said` | a proposal's due date is not the day said, or a date is given where no single day was said ("next week") | a date on a task is one somebody said |
| `no_name_or_pronoun` | a task title names the client or calls them he or she | the task is already tied to the client |

A proposal is matched to an expected commitment by a word its title must
contain and a line it must cite. The checks are unit-tested on hand-made
proposals in `backend/tests/test_session_commitment_eval_scorers.py`.

## Cases

Every session is on Wednesday 14 October 2026.

| Case | Session | Expected |
|---|---|---|
| `referral-by-friday` | the clinician will send a referral by Friday; the client is to practice a breathing exercise | the referral, due 16 October; not the exercise |
| `homework-only` | a thought log and journaling, both the client's | nothing |
| `offer-accepted` | the clinician offers to find a workbook, the client says yes | the workbook, undated |
| `offer-declined` | an offer of a support group list, declined | nothing |
| `two-with-different-times` | a call to the school counselor tomorrow; the FMLA form back next week; the client drops off a form Monday | the call due 15 October; the form with "next week" and no date; not the client's drop-off |
| `only-considered` | a psychiatry referral "down the line" | nothing |
| `done-in-the-session` | the release form was sent during the session; the client will sign it | nothing |
| `next-session-and-an-invite` | next Tuesday's session, and a calendar invite the clinician will send | the invite only |
| `client-and-clinician-each-promise` | the client will call the insurer and invite a sister; the clinician will check with the psychiatrist | the psychiatrist only |
| `dictated-after-the-session` | the clinician's dictation after the client leaves: email the records request by the 20th | the email, due 20 October |
| `someone-else-will` | a parent and a doctor will send records | nothing |
| `before-next-time` | the clinician will write up the safety plan before the next session | the write-up, undated |

## Running it

Always on the model the note path uses (Sonnet 4.6 on Bedrock today):

```bash
scripts/run-session-commitment-eval.sh --model bedrock:us.anthropic.claude-sonnet-4-6
scripts/run-session-commitment-eval.sh --model bedrock:us.anthropic.claude-sonnet-4-6 --runs 3
```

## Recorded run, 2026-10-18

Sonnet 4.6 on Bedrock (`us-east-1`), three runs of each of the twelve cases:
36 of 36 passed. Recall 1.0 (24 of 24 expected commitments), precision 1.0 (no
proposal the clinician did not make), client work given to the clinician 0,
dates right 24 of 24 (the nine dated ones on the day said, the fifteen others
undated), no call failed.
