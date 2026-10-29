# Imported notes and the chart

*Design note, 2026-10-17. Covers the records-system archive import (today:
SimplePractice) and the single-document note import, and what each should do
for the client's chart.*

## What a clinician moving practices needs

Narrative notes are enough for the old record. Nobody edits a signed note from
another system; what matters is that every note is there, on the right client,
with the right date, readable, and visibly marked as having come from
elsewhere. Re-structuring a signed record into SOAP fields would look like
altering it, and is not wanted.

What is missed on day one is the **chart**: history, medications, allergies
and diagnoses do not fill themselves from the imported notes, so every chart
starts empty and every chart-fed draft starts knowing nothing. The clinician
retypes it by hand, client by client.

So the work, in order:

1. Imported notes are readable and say where they came from.
2. Chart updates can be reviewed from the client's chart, not only from the
   note that proposed them, and the review reads well at transfer volume.
3. An archive import proposes the chart from each client's recent notes, as
   suggestions the clinician accepts, the same way a drafted note does.

## 1. Readable, and marked

**Found on the local stack (2026-10-17):** an archive-imported progress note
opened with its body in the left "Transcript" pane and `Nothing entered` in
the note itself; a chart note opened as `Nothing entered` with nothing else
on the page. The importer stores a narrative note the way the registry
defines it, `{"note": {"body": …}}`, and the web app projected a narrative
note from a top-level `body`. Every narrative note the backend writes (the
importer, chat's "save as note", a drafted narrative) hit this; the unit
tests mocked the flat shape and the archive specs never opened a note.

Beside that, an archive note was labelled `AI Generated`, offered `Sign note`
and `Add addendum`, was titled `Narrative Note` whatever it had been
(progress note, chart note, treatment plan), and nothing on any screen said
it came from another system. `Psychotherapy note` notes were titled
`Psychotherapy Note Note`.

**Decisions.**

- The web app reads a narrative note from `note.body`, accepting the flat
  shape for rows that have it, and writes edits in the registry shape.
- An archive session lands in the `imported` status that the single-document
  import already uses (#1675), with the document's signer as `source_signer`.
  A data migration moves the archive sessions already imported. Everything
  that status already means applies: no signing here, no Review listing,
  chart proposals decided on the page.
- A note the archive imported without a session (chart note, administrative
  note, treatment plan, psychotherapy note) carries its provenance in its
  content under `__source`. The note API now reads that into a `source`
  object (`system`, `kind`, `title`, `signed_by`, `signed_at`, `visit_date`)
  and marks the note `imported`. The refusals that protect a reference copy
  (sign, unlock, addendum) apply to these notes too.
- Every imported note shows one notice: **Imported from SimplePractice**,
  then what it was and when: `Progress note for the visit on September 25,
  2026.` and, when the document states one, `Signed there by Avery Provider
  on September 27, 2026.` The same notice serves both import paths; for a
  single-document import with no named system it reads `Imported`.
- Note headings use the document's own title for an imported note (`Chart
  note`, `Treatment plan`), and never append "note" to a label that already
  ends in it.
- List rows show the visit date for an imported note, and `Imported` in the
  status column, and the system in the source column.
- The `AI Generated` badge is never shown on an imported note.

The archive e2e now opens a progress note, a chart note and a psychotherapy
note and asserts the body is visible, the notice names the system, and no
sign action is offered.

## 2. Reviewing chart updates

### Where

Proposals are rows in `note_chart_proposals`, pending until decided, so they
can be reviewed any time. Today the only door is the note that proposed them.
Three doors, one component:

1. **At the note** (exists): the sign step of a drafted note, and the page of
   an imported note.
2. **On the client's chart** (new): a *Chart updates* strip at the top of the
   Chart card, above the tabs, shown only when something is pending. This is
   the before-the-session door: the clinician opens the client, sees what is
   waiting, decides it, and the draft that follows is written against a
   current chart. Grouped by the note that proposed it, newest first, each
   group headed by the note's date and origin (`From the note of Sep 25,
   2026, imported from SimplePractice`).
3. **On the dashboard** (later): a `Chart updates waiting` count on a Today
   row, linking to the client with the strip open. Needs a per-client count
   in the dashboard summary; a separate change.

Going to each client before their appointment is the right default. A
practice-wide queue is not: chart updates are decided with the client's chart
in view, and a list that strips them of that context invites rubber-stamping.
The dashboard count points at the client; it does not host the decision.

API: `GET /api/patients/{id}/chart-proposals?pending=1` returns pending
proposals across the client's notes with the note's date, type and source, in
the same response shape as the per-note listing. Decisions keep the per-note
route.

### How it reads

The current row is a bordered card per proposal: a long label (`Social
history and supports: Supports`), two uppercase section labels (`ON THE CHART
NOW`, `PROPOSED`), the evidence as a blockquote, and three buttons per card.
Four proposals fill a screen; a transfer client has ten or more, and the eye
finds no structure in a stack of identical cards.

Redesign, a **ledger**: proposals grouped under their chart section (`Social
history and supports`), each row a field name on the left and the proposed
text on the right with the changed span marked, `Now:` beneath only when the
chart has something to replace. A decided row collapses to one line with its
outcome. The group header carries the one bulk action: `Accept all` for the
section, so a clean transfer is a few clicks and a dirty one is read row by
row. Edit and Discard stay per row, as quiet text buttons; Accept stays a
button.

The one memorable element is the **source mark**: each proposal cites the
paragraph of the document it came from. Instead of quoting it inline, the row
ends in a small `¶ 3` mark; hovering shows the paragraph, clicking scrolls the
*Original document* pane to it and lights it. The same marks appear beside
each imported note field that the document grounds, replacing the separate
`from your note` badge with a link to *where*. This is the answer to "the
source seemed to be the whole document": the document is the source, and
every field and proposal now points at its line in it.

Layout, imported session page (desktop):

```
┌──────────────────────────────┬────────────────────────────────────┐
│ Original document            │ Progress note                      │
│                              │ ┌ Imported from SimplePractice ─┐  │
│ ¶1 Client arrived on time…   │ │ Progress note for the visit on │  │
│ ¶2 Mood was described as…    │ │ Sep 25, 2026. Signed there by  │  │
│ ¶3 Client reports sleep has  │ │ Avery Provider on Sep 27.      │  │
│    improved to six hours…    │ └────────────────────────────────┘  │
│ ¶4 Worry remains highest…    │ (note body)                        │
│                              │                                    │
│                              │ Chart updates          Accept all  │
│                              │ Social history and supports        │
│                              │  Supports   Sister and a neighbor ¶3│
│                              │  Relationships  Married, two…    ¶1│
│                              │ Medications                        │
│                              │  Start buspirone 5 mg, twice daily ¶6│
└──────────────────────────────┴────────────────────────────────────┘
```

Client chart (strip above the tabs):

```
┌ Chart ───────────────────────────────────────────────────────────┐
│ Chart updates waiting · 5                              Review ▾ │
│  From the note of Sep 25, 2026 · imported from SimplePractice    │
│   Social history and supports                      Accept all   │
│    Supports        Sister and a neighbor.             ¶3  Edit · Discard │
│    Relationships   Married, two children.             ¶1  Edit · Discard │
│   Medications                                                   │
│    Start buspirone 5 mg, twice daily                  ¶6  Discard │
│ [Notes 6] [Intake] [Documents] …                                 │
└──────────────────────────────────────────────────────────────────┘
```

Type and color are the app's own (DM Sans, Fraunces for the card title, the
warm oklch tokens). The section labels drop the uppercase tracking; field
names are medium weight at body size; the changed span keeps the existing
soft green `mark`. No new palette, no new face: the distinction is
structural, not decorative.

Reviewed against the default: a first draft reached for a card per proposal
with an eyebrow label and a checkbox column. Cards are what exists and what
reads badly at volume; checkboxes imply a form to submit, and these are
decided one by one, immediately. The ledger with per-section `Accept all` and
the paragraph marks is the version kept.

## 3. Filling the chart from an archive import

After an archive applies, for each client that received notes, a proposal
call reads their most recent progress note, and another their treatment
plan, each paragraph-numbered, with the client's chart as it stands. Each
reply's proposals are stored pending on the note they came from, origin
`document`, citing its paragraphs, exactly as a single-document import's are,
so the marks on a note's page point at that note's own text. A chart note (a
voicemail, a scheduling remark) is not read; a psychotherapy note never is.
Nothing lands on the chart until accepted.

Diagnoses need no model: the export's ICD-10 codes are in each note's
`__source`. They are offered beside the proposals as `Add to problem list`
rows, the same affordance a drafted note's stated diagnoses get.

The call runs inside the apply job, after the notes land, one client at a
time, and never fails the import: a client whose call fails has a run marked
`failed` and the usual *Pablo couldn't check this note for chart updates*
with Retry on the note. The import receipt gains one line, `Chart updates
from the notes are ready to review on N clients, at the top of each chart.`

The gate on which note types propose (`full_chart`) is about whether a drafted
note was written against the chart. It does not apply here: the purpose is to
seed the chart, and the note is a reference copy. A dedicated step entry point
takes the document and the chart and skips that check.

E2E on the local stack: the fake model answers a numbered `Client: Update on
<key>: <text>` paragraph with a proposal for that field. The archive fixture
gains nothing; the spec appends such a paragraph to one note's body in a
scratch copy of the fixture, imports, opens the client, and sees the strip
with that proposal and the export's three diagnoses offered; accepting writes
the chart.

## What this does not do

- No SOAP structuring of historical notes.
- No practice-wide proposal queue.
- Service codes, balances and contacts stay with the phase-2 import.

## Sequence

1. Readable and marked (this change).
2. Review on the client's chart, the ledger, the source marks.
3. Fill from the archive, diagnoses offered.
