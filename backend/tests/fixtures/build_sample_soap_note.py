"""Regenerate ``sample_soap_note.pdf``, the committed note-import fixture.

The extraction tests need a PDF that looks like something a therapist would
actually hand us: multi-page, a header block of labelled fields, the four SOAP
sections with inline sub-labels, a numbered homework list, and a signature
rule. Structure is the whole point -- that is what the extractor walks.

The CONTENT is entirely invented. It was written for this file rather than
copied or paraphrased from any real note, and the client is the obviously
fictional "Testy NotARealPatient". Keep it that way: this fixture is committed
to a public repository, so nothing derived from a real person's record may
ever land here, scrubbed or otherwise.

Run:  poetry run python backend/tests/fixtures/build_sample_soap_note.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

OUT = Path(__file__).with_name("sample_soap_note.pdf")

HEADER_FIELDS = [
    ("Client", "Testy NotARealPatient"),
    ("Date", "04/17/2026"),
    ("Session Type", "Individual therapy (50 minutes)"),
    ("Modality", "In person"),
    ("Diagnosis", "F43.22 &mdash; Adjustment disorder with anxiety"),
    ("Duration", "Approx. 5 months (beginning ~November 2025)"),
    ("Problem", "Workplace adjustment, sleep disruption, and avoidance of team settings"),
]

SUBJECTIVE = [
    "Client arrived on time and began by describing the past two weeks as "
    "&ldquo;steadier than the month before.&rdquo; He reported that the "
    "reorganisation at work has settled and that his new manager has been "
    "clearer about expectations, which he named as the single largest change.",
    "Client reported sleeping approximately six hours per night, up from the "
    "four to five hours reported in March. He continues to wake once most "
    "nights but described falling back asleep within about twenty minutes "
    "rather than lying awake until morning.",
    "Client described one difficult episode on the Wednesday of the prior "
    "week, when a project review was rescheduled with little notice. He "
    "reported a familiar tightening in the chest and a strong urge to call in "
    "sick, and noted that he attended anyway and that the meeting &ldquo;was "
    "fine, actually.&rdquo;",
    "Client reported continued reluctance to speak in larger team settings, "
    "which he framed as the piece that has moved least. He described "
    "rehearsing comments silently and then deciding not to make them, and "
    "expressed frustration at the gap between what he intends and what he "
    "does.",
    "Client denied any change to caffeine or alcohol use and reported "
    "resuming twice-weekly evening walks, which he connected to the improved "
    "sleep. He reported that his partner has noticed him being &ldquo;less "
    "wound up in the evenings.&rdquo;",
]

OBJECTIVE = [
    "<b>Appearance:</b> Well groomed, dressed appropriately for the weather, "
    "no psychomotor agitation observed.",
    "<b>Affect/Mood:</b> Affect was congruent and fuller in range than in "
    "recent sessions. Client smiled spontaneously twice while recounting the "
    "rescheduled review, which is a marked change from the flat presentation "
    "recorded in February.",
    "<b>Speech:</b> Normal rate, rhythm and volume. No pressure of speech.",
    "<b>Thought Process:</b> Linear and goal directed throughout. Client "
    "tracked the thread of the session without redirection and returned to "
    "his own earlier point unprompted on two occasions.",
    "<b>Cognition:</b> Alert and oriented. Attention and recall were intact "
    "across the hour, with no evidence of the distractibility noted earlier "
    "in treatment.",
    "<b>Risk Assessment:</b> Client denied suicidal ideation, homicidal "
    "ideation, and self harm urges when asked directly. No access concerns "
    "identified. No indication for a safety plan revision at this time.",
    "<b>Insight/Judgment:</b> Good. Client independently connected the "
    "avoided comments in team meetings to the same anticipatory pattern he "
    "had described about the project review, without prompting from the "
    "clinician.",
]

ASSESSMENT = [
    "Client continues to meet criteria for adjustment disorder with anxiety, "
    "with a clear and sustained trajectory of improvement across sleep, "
    "mood range and behavioural approach over the past six weeks.",
    "<b>Functional Impact:</b> Occupational functioning has improved "
    "materially. The decision to attend the rescheduled project review "
    "despite a strong avoidance urge is the most concrete evidence of change "
    "so far in treatment, and is the first instance the client has reported "
    "of approaching rather than withdrawing under acute anticipatory anxiety.",
    "<b>Treatment Response:</b> Client has responded well to the graded "
    "exposure framing introduced in February and to the sleep hygiene work "
    "that preceded it. Gains appear durable rather than situational, given "
    "they have held across a period that included an organisational change "
    "the client had previously identified as his main stressor.",
    "<b>Remaining Target:</b> Speaking in larger team settings remains the "
    "least changed domain and is now the natural focus. The rehearse-then-"
    "withhold pattern the client described is well suited to the same graded "
    "approach that worked for meeting attendance, at a smaller step size.",
    "<b>Protective Factors:</b> Stable partnership, engaged and consistent "
    "attendance, good insight, and a demonstrated willingness to tolerate "
    "discomfort in service of a stated goal.",
]

PLAN = [
    "<b>Continue:</b> Weekly individual sessions, with graded exposure "
    "extended from meeting attendance to verbal participation.",
    "<b>Introduce:</b> A hierarchy for speaking in groups, beginning with a "
    "single prepared question in a meeting of four or fewer people, before "
    "progressing to unprepared contributions in larger settings.",
    "<b>Monitor:</b> Sleep duration and night waking, which remain the "
    "clearest early indicator of deterioration for this client.",
]

HOMEWORK = [
    "Client to ask one prepared question in a small team meeting before the "
    "next session, and to record what he expected to happen alongside what "
    "actually happened.",
    "Client to continue twice-weekly evening walks and to note sleep onset on "
    "the nights that follow them.",
    "Client to bring the record of expectation versus outcome to the next "
    "session so the two can be compared directly.",
]

CLOSING = [
    "<b>Session Frequency:</b> Weekly sessions to continue. Client and "
    "clinician agreed to review frequency in six weeks against progress on "
    "the group-speaking hierarchy.",
    "<b>Risk Management:</b> No current risk indicators. Standard between-"
    "session contact information reviewed and confirmed.",
    "<b>Next Session:</b> Friday, April 24, 2026 at 2:00 pm, in person.",
]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Heading1"], fontSize=15, spaceAfter=2),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontSize=10,
            textColor="#555555",
            spaceAfter=12,
        ),
        "field": ParagraphStyle(
            "field", parent=base["Normal"], fontSize=10, leading=15, spaceAfter=2
        ),
        "section": ParagraphStyle(
            "section",
            parent=base["Heading2"],
            fontSize=12,
            spaceBefore=14,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            spaceAfter=7,
        ),
    }


def build() -> Path:
    st = _styles()
    story: list[object] = [
        Paragraph("INDIVIDUAL THERAPY PROGRESS NOTE", st["title"]),
        Paragraph("Solo Practice", st["subtitle"]),
    ]

    for label, value in HEADER_FIELDS:
        story.append(Paragraph(f"<b>{label}:</b> {value}", st["field"]))
    story.append(Spacer(1, 10))

    for heading, paragraphs in (
        ("S &mdash; Subjective (client report)", SUBJECTIVE),
        ("O &mdash; Objective (clinical observations)", OBJECTIVE),
        ("A &mdash; Assessment (clinical formulation)", ASSESSMENT),
        ("P &mdash; Plan (treatment plan &amp; next steps)", PLAN),
    ):
        story.append(Paragraph(heading, st["section"]))
        for para in paragraphs:
            story.append(Paragraph(para, st["body"]))

    story.append(Paragraph("Homework / Between-Session Tasks:", st["section"]))
    for n, item in enumerate(HOMEWORK, start=1):
        story.append(Paragraph(f"{n}. {item}", st["body"]))

    story.append(Spacer(1, 8))
    for para in CLOSING:
        story.append(Paragraph(para, st["body"]))

    story.append(Spacer(1, 22))
    story.append(Paragraph("_" * 44, st["field"]))
    story.append(Paragraph("Clinician Signature / Credentials / Date", st["field"]))

    SimpleDocTemplate(
        str(OUT),
        pagesize=LETTER,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        title="Individual Therapy Progress Note",
        author="Pablo test fixture",
    ).build(story)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size} bytes)")
