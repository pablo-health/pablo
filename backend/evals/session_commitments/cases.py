# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the session-commitment eval.

Each case is one invented session transcript, about no one, and the whole set
of things the clinician said they would do in it. Every session is on
Wednesday 14 October 2026, so "Friday" is the 16th. Segment ids count the
transcript's lines from 0, as the call numbers them.

Most cases put the client's own work beside the clinician's, because telling
the two apart is the hard part: a task the client agreed to do is homework,
never a task for the clinician.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

VISIT = date(2026, 10, 14)


@dataclass(frozen=True)
class ExpectedCommitment:
    name: str
    """How the report names it."""
    any_of: tuple[str, ...]
    """The task title contains at least one of these (lower case)."""
    evidence: tuple[int, ...]
    """Lines it may cite; it must cite at least one."""
    due_date: date | None = None
    """The day said; ``None`` when no single day was said, and then none may be given."""


@dataclass(frozen=True)
class CommitmentCase:
    name: str
    lines: tuple[str, ...]
    expected: tuple[ExpectedCommitment, ...] = ()
    """Exactly these; empty means the session commits the clinician to nothing."""
    client_work: tuple[str, ...] = ()
    """Words naming the client's own work (or someone else's) in this session. A
    proposal containing one has given the clinician a task that is not theirs."""
    client_name: str = "Dana"

    @property
    def transcript(self) -> str:
        return "\n".join(self.lines)


def _t(*turns: tuple[str, str]) -> tuple[str, ...]:
    """Turns as transcript lines, a few seconds apart."""
    return tuple(
        f"[{(i * 23) // 60:02d}:{(i * 23) % 60:02d}] {who}: {said}"
        for i, (who, said) in enumerate(turns)
    )


T, C = "Therapist", "Client"

ALL_CASES: tuple[CommitmentCase, ...] = (
    CommitmentCase(
        name="referral-by-friday",
        lines=_t(
            (T, "How has sleep been since we last talked, Dana?"),
            (C, "Bad. I keep waking up at three. I think I need to see someone about meds."),
            (T, "That makes sense. I'll send a referral to Dr. Okafor's office by Friday."),
            (T, "Meanwhile, try the breathing exercise we practiced, every night before bed."),
            (C, "Okay, I'll try the breathing thing."),
        ),
        expected=(
            ExpectedCommitment(
                "referral", ("referral", "refer"), evidence=(2,), due_date=date(2026, 10, 16)
            ),
        ),
        client_work=("breathing", "exercise"),
    ),
    CommitmentCase(
        name="homework-only",
        lines=_t(
            (T, "For this week, I'd like you to keep the thought log we went over."),
            (C, "I'll do it every evening. And I'll journal after my runs."),
            (T, "Good. Bring it next time and we'll look at it together."),
            (C, "Will do."),
        ),
        client_work=("log", "journal", "run", "bring"),
    ),
    CommitmentCase(
        name="offer-accepted",
        lines=_t(
            (C, "I read that there are workbooks for insomnia."),
            (T, "There are. Would it help if I found you one I trust?"),
            (C, "Yes, please, that would help."),
            (T, "Okay, I'll look for one and send it over."),
        ),
        expected=(ExpectedCommitment("workbook", ("workbook", "book"), evidence=(1, 2, 3)),),
    ),
    CommitmentCase(
        name="offer-declined",
        lines=_t(
            (T, "Would you like me to send you a list of support groups nearby?"),
            (C, "No, thanks. I'm not ready for a group."),
            (T, "That's fine. We can come back to it whenever you want."),
        ),
        client_work=("support group", "group"),
    ),
    CommitmentCase(
        name="two-with-different-times",
        lines=_t(
            (C, "My son's school keeps calling me about his behavior."),
            (T, "With your permission, I'll call the school counselor tomorrow."),
            (C, "Yes, go ahead."),
            (C, "Also my job needs the FMLA paperwork."),
            (T, "I'll fill out the FMLA form and have it back to you next week."),
            (C, "I'll drop the employer's part off at your office Monday."),
        ),
        expected=(
            ExpectedCommitment(
                "school counselor",
                ("counselor", "school"),
                evidence=(1, 2),
                due_date=date(2026, 10, 15),
            ),
            ExpectedCommitment("fmla", ("fmla", "leave"), evidence=(4,)),
        ),
        client_work=("drop", "employer's part"),
    ),
    CommitmentCase(
        name="only-considered",
        lines=_t(
            (C, "Sometimes I wonder if medication would help."),
            (
                T,
                "We could think about a psychiatry referral down the line, if things don't "
                "improve.",
            ),
            (C, "Let's see how the next month goes first."),
        ),
        client_work=("medication",),
    ),
    CommitmentCase(
        name="done-in-the-session",
        lines=_t(
            (T, "I just sent the release form to your email while we were talking."),
            (C, "Got it, I see it."),
            (T, "Sign it whenever you get a chance this week."),
            (C, "I'll sign it tonight."),
        ),
        client_work=("sign",),
    ),
    CommitmentCase(
        name="next-session-and-an-invite",
        lines=_t(
            (T, "So same time next Tuesday?"),
            (C, "Yes, Tuesday works."),
            (T, "Great, I'll send you a calendar invite with the video link."),
            (C, "Thanks. See you Tuesday."),
        ),
        expected=(ExpectedCommitment("invite", ("invite", "calendar"), evidence=(2,)),),
    ),
    CommitmentCase(
        name="client-and-clinician-each-promise",
        lines=_t(
            (C, "I'll call the insurance company about the claim they denied."),
            (T, "Good. And I'll check with your psychiatrist about the dose change you mentioned."),
            (C, "Thank you."),
            (C, "I'm also going to ask my sister to come to a session."),
        ),
        expected=(ExpectedCommitment("psychiatrist", ("psychiatrist", "dose"), evidence=(1,)),),
        client_work=("insurance", "sister", "claim"),
    ),
    CommitmentCase(
        name="dictated-after-the-session",
        lines=_t(
            (T, "Okay, take care, see you in two weeks."),
            (C, "Bye, thanks."),
            (
                T,
                "Dictating after the session. Plan: email Dr. Patel the records request "
                "by the 20th. Continue weekly CBT.",
            ),
        ),
        expected=(
            ExpectedCommitment(
                "records request",
                ("records", "patel"),
                evidence=(2,),
                due_date=date(2026, 10, 20),
            ),
        ),
    ),
    CommitmentCase(
        name="someone-else-will",
        lines=_t(
            (C, "My mom said she'll bring the old school records next time."),
            (T, "That would be helpful to see."),
            (C, "And my doctor is going to send over my bloodwork."),
            (T, "Great, we can go over both next time."),
        ),
        client_work=("records", "bloodwork", "school"),
    ),
    CommitmentCase(
        name="before-next-time",
        lines=_t(
            (T, "We'll keep working on the safety plan next time."),
            (C, "Okay."),
            (T, "Before then I'll write up what we have so far and send you a copy."),
            (C, "That would be good. I'll put it on my fridge."),
        ),
        expected=(
            ExpectedCommitment(
                "write up the plan", ("write", "copy", "safety plan", "send"), evidence=(2,)
            ),
        ),
        client_work=("fridge",),
    ),
)

CASES_BY_NAME = {c.name: c for c in ALL_CASES}
