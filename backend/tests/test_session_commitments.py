# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the clinician said they would do: the checks made on the call's answer.

The call proposes; these checks decide what reaches the clinician. Each test
names the mistake it stops: the client's own work proposed as the clinician's,
a citation to a line the session lacks, a date nobody said, a date read off
the wrong calendar, a psychotherapy note's session read at all.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.chart_proposals.commitments import build_prompt, parse_commitments, propose_commitments
from app.chart_proposals.families import SESSION_TASK
from app.chart_proposals.models import Commitment
from app.chart_proposals.step import ChartProposalStep, proposes_commitments
from app.models import Transcript
from app.notes import get_default_registry
from app.repositories import InMemoryChartHistoryRepository, InMemoryChartProposalRepository
from app.services.note_generation_service import MockNoteGenerationService

WEDNESDAY = date(2026, 10, 14)
FRIDAY = date(2026, 10, 16)

LINES = [
    "[00:01] Therapist: How did the week go?",
    "[00:20] Client: Rough. I still haven't heard back about a psychiatrist.",
    "[00:41] Therapist: I'll send the referral to Dr. Lee by Friday.",
    "[01:02] Therapist: And this week, try the breathing exercise before bed.",
    "[01:15] Client: I'll call my sister and tell her about the move.",
    "[01:30] Therapist: I can also look for a sleep workbook for you.",
    "[01:34] Client: Yes please.",
]
SEGMENTS = dict(enumerate(LINES))
TRANSCRIPT = Transcript(format="txt", content="\n".join(LINES))


def _item(task: str, ids: list[Any], due_said: str = "", due_date: str = "") -> dict[str, Any]:
    return {
        "task": task,
        "evidence_segment_ids": ids,
        "due_said": due_said,
        "due_date": due_date,
    }


def _parse(*items: dict[str, Any]) -> list[Any]:
    return parse_commitments({"commitments": list(items)}, SEGMENTS, WEDNESDAY)


def test_a_commitment_the_clinician_said_is_proposed_with_the_day_said() -> None:
    [referral] = _parse(_item("Send the referral to Dr. Lee", [2], "by Friday", "2026-10-16"))

    assert referral.field_key == SESSION_TASK
    assert referral.proposed_text == "Send the referral to Dr. Lee"
    assert referral.commitment == Commitment(due_date=FRIDAY, due_said="by Friday")
    assert [e.segment_id for e in referral.evidence] == [2]


def test_the_clients_own_work_is_never_the_clinicians() -> None:
    """Only the client spoke it: the client's to do, whatever the call said."""
    assert _parse(_item("Call the sister about the move", [4])) == []


def test_an_accepted_offer_cites_both_lines_and_is_kept() -> None:
    [workbook] = _parse(_item("Look for a sleep workbook", [5, 6]))
    assert workbook.commitment == Commitment()


def test_a_citation_the_session_lacks_drops_the_commitment() -> None:
    assert _parse(_item("Send the referral", [2, 40])) == []
    assert _parse(_item("Send the referral", [])) == []


def test_a_date_whose_words_are_not_in_the_cited_lines_is_dropped() -> None:
    """A deadline nobody said is not a deadline: proposed undated."""
    [referral] = _parse(_item("Send the referral", [2], "by Monday", "2026-10-19"))
    assert referral.commitment == Commitment()


def test_words_without_a_single_day_keep_the_words_and_no_date() -> None:
    lines = {0: "[00:05] Therapist: I'll email the school counselor next week."}
    [email] = parse_commitments(
        {"commitments": [_item("Email the school counselor", [0], "next week", "")]},
        lines,
        WEDNESDAY,
    )
    assert email.commitment == Commitment(due_date=None, due_said="next week")


def test_a_day_before_the_visit_or_far_past_it_is_not_believed() -> None:
    [early] = _parse(_item("Send the referral", [2], "by Friday", "2026-10-09"))
    [late] = _parse(_item("Send the referral", [2], "by Friday", "2027-10-15"))
    # The words were said, so they stay beside the task; the date read from them does not.
    assert early.commitment == Commitment(due_date=None, due_said="by Friday")
    assert late.commitment == Commitment(due_date=None, due_said="by Friday")


def test_the_same_task_twice_is_proposed_once_and_keys_stay_distinct() -> None:
    kept = _parse(
        _item("Send the referral to Dr. Lee", [2]),
        _item("Send the referral to Dr. Lee.", [2]),
        _item("Fax the records to Dr. Lee", [2]),
    )
    assert [p.proposed_text for p in kept] == [
        "Send the referral to Dr. Lee",
        "Fax the records to Dr. Lee",
    ]
    assert [p.item_key for p in kept] == ["S2", "S2.2"]


def test_the_prompt_gives_the_calendar_from_the_visit() -> None:
    """Without the weekday beside each date, "Friday" is arithmetic the model can miss."""
    prompt = build_prompt("[S0] [00:01] Therapist: hi", WEDNESDAY)
    assert "- Wednesday 2026-10-14 (the session)" in prompt
    assert "- Friday 2026-10-16" in prompt


def test_a_failed_call_proposes_nothing_and_says_so() -> None:
    def broken(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError

    proposals, error = propose_commitments(broken, TRANSCRIPT, WEDNESDAY)
    assert proposals is None
    assert error == "TimeoutError"


class _Answering(MockNoteGenerationService):
    def __init__(self, reply: dict[str, Any]) -> None:
        super().__init__()
        self.reply = reply
        self.calls = 0

    def chart_proposal_completion(self) -> Any:
        def complete(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.calls += 1
            return self.reply

        return complete


def test_a_psychotherapy_notes_session_is_never_read_for_tasks() -> None:
    registry = get_default_registry()
    assert not proposes_commitments(registry.get("psychotherapy"))
    assert proposes_commitments(registry.get("soap"))

    generator = _Answering({"commitments": [_item("Send the referral", [2])]})
    step = ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())
    drafted = step.draft(generator, registry.get("psychotherapy"), None, TRANSCRIPT, {})
    assert drafted is None
    assert generator.calls == 0


def test_a_soap_session_proposes_its_tasks_with_no_chart_run() -> None:
    generator = _Answering(
        {"commitments": [_item("Send the referral", [2], "by Friday", "2026-10-16")]}
    )
    step = ChartProposalStep(InMemoryChartProposalRepository(), InMemoryChartHistoryRepository())
    drafted = step.draft(
        generator, get_default_registry().get("soap"), None, TRANSCRIPT, {}, visit_date=WEDNESDAY
    )
    assert drafted is not None
    assert drafted.proposals == []
    assert drafted.commitments is not None
    assert [c.proposed_text for c in drafted.commitments] == ["Send the referral"]
