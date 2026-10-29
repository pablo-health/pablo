# Materiality: whether a history proposal is worth the clinician's attention

Module: `backend/app/chart_proposals/materiality.py`

Whether a history proposal is worth the clinician's attention, decided in code.

The proposal call is asked to propose only lasting changes, and mostly does;
what still gets through is a restatement, a passing event, a detail the
clinician asked about rather than one the client told, the present visit
written into a field about the past, or a fact the chart already keeps in
another field. Each costs the clinician a discard at sign, and enough of them
teach a clinician to stop reading the step. So the reply's history proposals
are checked here after their evidence is, and one that fails is not offered.
It is kept on the run with the reason (:class:`Considered`): considered, not
offered.

The medication list and the allergies are not checked here; their families
have their own rules (:mod:`.families`).

Words are compared normalised: lowercase, numbers as digits, dates as ISO
where they parse, stop words out, a plural ``s`` dropped. The word lists are
committed data beside this module (``materiality.json``), each documented
there. A history proposal is offered only when all four hold:

1. **Novelty.** The proposal has a word or a number the chart's text for the
   field does not, and is not that text reworded: at a token-set Jaccard
   similarity of :data:`PARAPHRASE_JACCARD` or more, what it adds must carry
   a state marker, an entity, a name or a number. Where the extraction beside
   the draft covers the field (a type that prints it from the chart), the
   extraction must have found the fact said this visit: it leaves out a field
   the visit only repeated. It files a fact by its best guess at the field,
   so a statement it filed under another field counts when it shares at least
   :data:`ELSEWHERE_MIN_WORDS` of the words the proposal adds
   (:func:`heard_about`).
2. **Lasting state.** The new words include a state-change marker
   (``started``, ``moved``, ``finalized``, ``laid off``, ``no longer``) or a
   new named entity (a person, place, clinician, program, diagnosis or
   treatment the chart does not name, or a capitalised name it lacks). New
   words that include a transient-event word (an appointment, a scheduled
   interview, a weekday, "expecting") with no state marker are not lasting.
   And a line the proposal rests on (the client's, or the clinician's
   dictation) says the change itself: it carries a marker of a kind that
   grounds one the proposal adds, or the entity or name the proposal adds.
   The markers are grouped by kind, so a client's "started" grounds a note's
   "began"; and a few kinds ground each other (``grounds`` in the lists), so
   a client's "I don't work there anymore" grounds a note's "let go". A
   marker phrase matches across up to :data:`MARKER_GAP` stop words, so the
   client's "they let me go" is the note's "let go". A group the lists scope
   to fields (work, to ``work_school``) is a marker, and grounds one, only
   in those fields: "not working" about a sleep aid is not a job change.
   "Weekly therapy begun" written from "Three sessions." is an inference,
   not something said.
3. **Source.** A transcript proposal rests on what the client said or on what
   the clinician dictated after the client's last line: at least one line it
   cites is one of those. One citing only the clinician's interview lines
   (questions and reflections before the client's last line) is not offered.
   A cited question beside the client's answer is fine; that is how a
   substance screen is evidenced. A transcript with no client line (one
   microphone, or a dictation) cannot tell the two apart and is not checked.
4. **Not already elsewhere.** The fact is not kept in another of the chart's
   places: new words that one other history field already holds (at least
   :data:`ELSEWHERE_MIN_WORDS` of them), a diagnosis the problem list already
   has, a medication the list has or this visit proposes, a dose, or a lab
   result. A history field takes neither of the last two; a substance field
   and ``medication_trials`` record amounts of their own, but a substance
   field still takes no dose of a medication the list carries. And a field
   about the past (``past_self_harm``, ``hospitalizations``,
   ``prior_diagnoses``, ``medication_trials``, ``trauma_history``) is not
   given this visit's present: new words with a present-time marker
   (``currently``, ``this visit``, ``most mornings``) and no past-time
   marker are refused as ``present-not-past``.

An empty field takes a baseline, a denial included: anything stated is new,
and lasting by being the field's first fact. Only the source, the
extraction and the problem list are checked for it.

The thresholds come from the longitudinal episodes' history proposals on the
model the product runs: every one those runs made, the ones their fixtures
expect and the ones they do not.

- :data:`PARAPHRASE_JACCARD` = 0.85. Similarity alone does not separate the
  two. An amendment keeps the chart's text and adds a clause, so expected
  amendments run from 0.11 to 0.90 against the chart, and unwanted ones from
  0.09 to 0.75. Similarity therefore refuses nothing on its own. Above 0.85 a
  proposal is a rewording unless what it adds carries something. The one
  expected proposal above 0.85, a divorce finalized on a date, carries both.
- :data:`ELSEWHERE_MIN_WORDS` = 2. A single word shared with another field
  ("sister") is a coincidence, not the fact.

Where the rules trade a missed proposal against an unwanted one, they prefer
silence: an unoffered change is still in the note, which the clinician signs,
and can be added to the chart by hand; an unwanted proposal is a discard at
every sign.
