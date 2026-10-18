# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The commitment call: what the clinician said in a session they would do.

"I'll send you a referral", "I'll call your psychiatrist this week", "let me
find you that workbook". After a draft, one small structured call reads the
session's transcript, each line numbered, and returns each thing the clinician
said they themself would do after the session, citing the lines that say it.
Each becomes a proposed task, reviewed beside the note's chart updates and
filed to the clinician's tasks, tied to the client, only when accepted.

What is checked here, after the call, rather than trusted to it:

* every commitment cites lines this transcript has, and at least one of them
  is the clinician's: a line only the client spoke can be the client's own
  work, never the clinician's commitment. (A transcript whose speakers cannot
  be told apart is read as it is.)
* a due date is kept only when the words that name it ("by Friday") are in
  the cited lines, and the day is on or after the visit and within the
  window a spoken deadline plausibly means. Otherwise the commitment is
  proposed undated, with the words, if any, beside it.

The client's own work (homework, practice between sessions) is not asked for
and is not proposed. It rides the same model and provider as the draft, and
like the chart proposal call it never fails the draft: an error proposes
nothing and is logged by its exception type only.
"""

from __future__ import annotations

import logging
import re
import string
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from ..notes.client_present import is_client_speaker
from ..services.source_attribution_service import format_transcript_with_segment_ids
from .evidence import cited_evidence, segment_texts
from .families import SESSION_TASK, TASK_TITLE_LIMIT
from .models import Commitment, DraftedProposal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..models import Transcript
    from ..services.note_generation_service import CompleteStructured
    from .models import Evidence

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You read the transcript of one therapy or psychiatry session and list what the "
    "clinician said they themself would do after the session. You state only what was "
    "said: you never infer, suggest or add. Return only the JSON object asked for."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "SessionCommitments",
    "properties": {
        "commitments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "evidence_segment_ids": {"type": "array", "items": {"type": "integer"}},
                    "due_said": {"type": "string"},
                    "due_date": {"type": "string"},
                },
                "required": ["task", "evidence_segment_ids", "due_said", "due_date"],
            },
        }
    },
    "required": ["commitments"],
}

#: How far ahead of the visit a spoken deadline is believed: a date past it is
#: more likely a misreading than a promise, so the commitment is offered undated.
_LATEST_DUE = timedelta(days=120)

#: How many days of calendar the prompt lists, for reading "Friday" as a day.
_CALENDAR_DAYS = 21

_INSTRUCTIONS = """\
List each thing the clinician said they will do themself after this session: send, \
write, call, email, fax, refer, look into, find, check with someone, fill out or sign \
something, coordinate with another provider. One item per thing. If the clinician said \
nothing of the kind, return an empty list: that is a common answer.

These are not commitments, and never go in the list:
- Anything the client is to do, however it is phrased ("you'll call your sister", \
"try the breathing exercise this week", "can you fill out the mood log"). That is the \
client's work.
- Things done during the session itself, or already done before it.
- Offers the client declined or left unanswered, and things the clinician only \
considered, suggested or might do ("we could think about a referral").
- Meeting again or seeing the client next time, unless the clinician said they will \
do something to arrange it (send a calendar invite, call to reschedule).
- What someone other than the clinician will do.

For each one give:
- task: what the clinician will do, as a short task title that starts with a verb, in \
the clinician's own words where possible ("Send the release form", "Call the \
psychiatrist about the dose"). Do not name the client or use a pronoun for them: the \
task is already tied to them. Other people are named by their role (the psychiatrist, \
the school counselor). At most 120 characters.
- evidence_segment_ids: the numbers (n in [Sn]) of the lines where the clinician says \
it, and the client's line that accepts an offer, if it was one.
- due_said: the exact words, copied from those lines, that say when ("by Friday", \
"tomorrow", "next week"), or "" if no time was said.
- due_date: the day those words name, as YYYY-MM-DD, read from the calendar below; \
"" when no time was said, or when the words name no single day ("next week", "soon", \
"before we meet again")."""


def _calendar(visit: date) -> str:
    days = [visit + timedelta(days=n) for n in range(_CALENDAR_DAYS)]
    lines = [
        f"- {d.strftime('%A')} {d.isoformat()}" + (" (the session)" if d == visit else "")
        for d in days
    ]
    return "\n".join(lines)


def build_prompt(indexed_transcript: str, visit: date) -> str:
    return "\n".join(
        [
            _INSTRUCTIONS,
            "",
            "Calendar, from the day of the session:",
            _calendar(visit),
            "",
            "Transcript (each line numbered [Sn]):",
            indexed_transcript,
        ]
    )


_SPEAKER = re.compile(r"^\[[\d:]+\]\s*([^:]+):")


def _speaker(text: str) -> str | None:
    match = _SPEAKER.match(text.strip())
    return match.group(1).strip() if match else None


def _clinician_said(evidence: Sequence[Evidence]) -> bool:
    """Whether a cited line is the clinician's. A transcript whose lines name no
    speaker cannot tell, and is not held against the commitment."""
    speakers = [_speaker(e.text) for e in evidence]
    if all(s is None for s in speakers):
        return True
    return any(s is not None and not is_client_speaker(s) for s in speakers)


_PUNCT = str.maketrans(dict.fromkeys(string.punctuation, " "))


def _words(text: str) -> str:
    return " ".join(text.lower().translate(_PUNCT).split())


def _due(item: Mapping[str, Any], evidence: Sequence[Evidence], visit: date) -> Commitment:
    """When it is due, only as far as the cited lines say it."""
    said = str(item.get("due_said") or "").strip()
    cited = " ".join(_words(e.text) for e in evidence)
    if not said or not _words(said) or f" {_words(said)} " not in f" {cited} ":
        return Commitment()
    raw = str(item.get("due_date") or "").strip()
    try:
        day = date.fromisoformat(raw) if raw else None
    except ValueError:
        day = None
    if day is not None and not (visit <= day <= visit + _LATEST_DUE):
        day = None
    return Commitment(due_date=day, due_said=said)


def _title(raw: Any) -> str:
    title = " ".join(str(raw or "").split()).rstrip(".")
    return title[:1].upper() + title[1:] if title else ""


def parse_commitments(
    reply: Mapping[str, Any], segments: Mapping[int, str], visit: date
) -> list[DraftedProposal]:
    """The reply's commitments that cite this transcript and a line of the clinician's."""
    kept: list[DraftedProposal] = []
    seen_titles: set[str] = set()
    used_keys: set[str] = set()
    raw = reply.get("commitments")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        title = _title(item.get("task"))
        if not title or len(title) > TASK_TITLE_LIMIT or _words(title) in seen_titles:
            continue
        evidence = cited_evidence(item.get("evidence_segment_ids"), segments)
        if evidence is None or not _clinician_said(evidence):
            continue
        seen_titles.add(_words(title))
        # Keyed on the line it was said on, which a redraft does not move, so a
        # dismissed commitment reworded by the next call is not offered again.
        key = f"S{evidence[0].segment_id}"
        n = 2
        while key in used_keys:
            key = f"S{evidence[0].segment_id}.{n}"
            n += 1
        used_keys.add(key)
        due = _due(item, evidence, visit)
        kept.append(
            DraftedProposal(
                field_key=SESSION_TASK,
                item_key=key,
                proposed_text=title,
                what_changed=due.due_said,
                evidence=evidence,
                commitment=due,
            )
        )
    return kept


def propose_commitments(
    complete: CompleteStructured, transcript: Transcript, visit: date
) -> tuple[list[DraftedProposal] | None, str | None]:
    """Ask what the clinician said they would do. Never raises: a failure returns no
    proposals and the exception's type name."""
    indexed = format_transcript_with_segment_ids(transcript.content)
    if not indexed:
        return [], None
    segments = segment_texts(indexed)
    try:
        reply = complete(SYSTEM_PROMPT, build_prompt(indexed, visit), RESPONSE_SCHEMA)
        return parse_commitments(reply, segments, visit), None
    except Exception as exc:
        logger.warning("Commitment call failed; the session proposes no tasks", exc_info=True)
        return None, type(exc).__name__
