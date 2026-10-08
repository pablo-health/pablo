# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cases for the chart-proposal eval.

Each case is a chart and one follow-up visit's transcript, both invented
here for the eval and about no one. The expectation is the whole set of
proposals: which fields, what each must and must not say, and which
transcript lines it may cite. Segment ids are the ones the proposal call
numbers the transcript with, counting from 0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.notes.chart_context import ChartContext, ChartHistoryField


@dataclass(frozen=True)
class ExpectedProposal:
    field_key: str
    must_contain: tuple[str, ...] = ()
    must_contain_any: tuple[str, ...] = ()
    """At least one of these, when given."""
    must_not_contain: tuple[str, ...] = ()
    evidence: tuple[int, ...] = ()
    """Lines the proposal may cite; it must cite at least one of them."""


@dataclass(frozen=True)
class ProposalCase:
    name: str
    chart: ChartContext
    transcript: str
    expected: tuple[ExpectedProposal, ...] = field(default_factory=tuple)
    """Exactly these fields are proposed; an empty tuple means no proposal at all."""


def _chart(**history: str) -> ChartContext:
    return ChartContext(
        allergy_status="nkda",
        history=tuple(ChartHistoryField(k, v, date(2026, 6, 10)) for k, v in history.items()),
    )


_DIVORCE_CHART = _chart(
    relationships="Married; separated, divorce in progress since June. Two teenage sons.",
    legal_custody="Divorce in progress; shared custody of both sons. No other legal involvement.",
    living_situation="Lives alone in an apartment since the separation.",
    work_school="Works full time as an accountant at a regional firm.",
    supports="Brother nearby; a close friend from college.",
    alcohol="One or two glasses of wine on weekends.",
)

DIVORCE_FINALIZED = ProposalCase(
    name="divorce-finalized",
    chart=_DIVORCE_CHART,
    transcript="""\
[00:00] Therapist: Good to see you. How have the last four weeks been?
[00:05] Client: Better than I expected, honestly. Sleep is still patchy but improving.
[00:14] Therapist: Still waking around three?
[00:17] Client: Two or three nights a week now, down from most nights.
[00:24] Therapist: And the sertraline, any side effects?
[00:28] Client: No, none. I take it every morning with breakfast.
[00:35] Therapist: Last time you said the divorce paperwork was moving.
[00:39] Client: It's done. The divorce was finalized on September 12th.
[00:45] Client: The judge signed it and that was that. It felt strange, but also a relief.
[00:53] Therapist: How are the boys taking it?
[00:57] Client: They're okay. They're with me every other weekend, same as before.
[01:05] Therapist: Still in the apartment?
[01:08] Client: Yes, same place. Work is the same too, busy season is starting.
[01:16] Therapist: Drinking?
[01:18] Client: Same as always, a glass or two of wine on the weekend.
[01:24] Therapist: Who have you been leaning on?
[01:27] Client: My brother mostly, like before.
[01:32] Therapist: Okay. We'll keep the sertraline at 100 and meet again in six weeks.
""",
    expected=(
        ExpectedProposal(
            field_key="relationships",
            must_contain=("separated", "finalized", "sons"),
            evidence=(7, 8),
        ),
        ExpectedProposal(
            field_key="legal_custody",
            must_contain=("custody", "finalized"),
            evidence=(7, 8),
        ),
    ),
)
"""One fact, two fields: the divorce changes the relationship and the legal record, and
each keeps what it said. A proposal for only one of them fails."""

UNCHANGED = ProposalCase(
    name="unchanged",
    chart=_DIVORCE_CHART,
    transcript="""\
[00:00] Therapist: How have things been since last time?
[00:04] Client: Pretty steady. Nothing much has changed.
[00:09] Therapist: Still separated, the divorce still going through?
[00:13] Client: Yes, still waiting on the lawyers. Same as before.
[00:19] Therapist: And work?
[00:21] Client: Same firm, same hours. It's fine.
[00:26] Therapist: Living situation the same?
[00:29] Client: Same apartment.
[00:32] Therapist: Alcohol?
[00:34] Client: Wine on the weekends, a glass or two. No change.
[00:40] Therapist: Your brother still around?
[00:43] Client: Yes, we had dinner on Sunday.
[00:48] Therapist: Mood?
[00:50] Client: Okay. Some low days, fewer than before.
[00:56] Therapist: Good. We'll continue everything as it is and meet in a month.
""",
)
"""Everything restated, nothing changed: no proposal."""

STOPPED_WORKING = ProposalCase(
    name="stopped-working",
    chart=_chart(
        work_school="Works full time as a dental hygienist at a family dental practice.",
        living_situation="Lives with husband and two children.",
    ),
    transcript="""\
[00:00] Therapist: What's been going on since we last met?
[00:04] Client: A lot, actually. I stopped working at the dental practice at the end of August.
[00:12] Client: They cut the hygiene schedule and let two of us go.
[00:18] Therapist: I'm sorry. How has that been?
[00:21] Client: Stressful about money, but I'm sleeping more. I've been applying to other offices.
[00:30] Therapist: Anything else change at home?
[00:33] Client: No, same house, my husband and the kids.
[00:39] Therapist: How's the anxiety?
[00:42] Client: Up and down. Worse on days I check the bank account.
[00:49] Therapist: Are you still taking the buspirone twice a day?
[00:53] Client: Yes, every day.
[00:57] Therapist: Okay. We'll keep it as is and meet in four weeks.
""",
    expected=(
        ExpectedProposal(
            field_key="work_school",
            must_contain=("dental",),
            must_contain_any=("no longer", "stopped", "until", "left", "former", "let go"),
            evidence=(1, 2),
        ),
    ),
)
"""The job ended: the chart keeps that she worked there and adds that she no longer does."""

ALL_CASES: tuple[ProposalCase, ...] = (DIVORCE_FINALIZED, UNCHANGED, STOPPED_WORKING)
