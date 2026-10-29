# Patient matching: which of the practice's patients does an outside record mean?

Module: `backend/app/patients/matching.py`

Which of the practice's patients does an outside record mean?

A calendar feed, a calendar import and an archive all arrive with a name, an
email, a code or a pair of initials, and each has to be tied to a chart or
put in front of the clinician as a question. This is the one place that
decides, so every one of them answers the same way.

A client belongs to the practice, not to one clinician, so the candidates are
every live chart in the practice (``practice_client_directory()``), not only
the ones the acting clinician holds a grant on. Otherwise a colleague's client
is invisible here and becomes a second chart. Each answer says which of its
charts the caller can see (``hidden_ids``); a caller never creates a chart or
books anything for one it cannot see, and tells the clinician who does see it.

How far a check looks depends on how strong its evidence is. The first three
are strong, and look across the whole practice: a certain match to a
colleague's client on any of them is that client. The last two rest on a name
alone, and look only at the charts the caller sees. Two clinicians can each
have a "J.A." without either being the other's; a colleague's chart that
matches only by name or initials never makes the caller's own match
uncertain, and never stands in the way of a new client.

The checks run in a fixed order, strongest first:

1. ``remembered`` — someone already said which patient this source's
   identifier means (``patient_source_mappings``). An answer is the
   clinician's own for a feed's client codes and names, and one calendar's
   for a calendar's series, shared by everyone who follows it; the hint says
   which (``scope``), and the identifier is looked up
   by its keyed digest (``identifiers.identifier_digest``).
2. ``name_and_dob`` — exactly one patient in the practice has this name and
   date of birth.
3. ``email`` — exactly one patient in the practice has this email. Some
   systems treat an email as unique to one client; Pablo does not, because
   families often share one address. So an email settles the question only
   when exactly one chart in the whole practice carries it, and it comes after
   name and date of birth: a child's record carrying a parent's email must
   land on the child's chart, and a shared family email must never outrank a
   unique name and date of birth.
4. ``full_name`` — exactly one of the caller's patients has this whole name,
   and no other of theirs shares its first and last word.
5. ``initials`` — exactly one of the caller's patients has these initials.
   Two of theirs sharing initials stay a question; the answer is remembered,
   so it is asked once per series.

A name that agrees only on its first and last word ("Mary Ann Smith" and a
chart for Mary Smith, or "Pablo Bear" and a chart typed as Pablo A / Bear) is
never certain: it may be someone else, so it is offered as a possible match.

A remembered answer always settles the question. If the patient it names is
no longer a live chart, there is no match at all — never a weaker guess in
its place — and the caller asks again.

A remembered answer can also be that the identifier is not a client at all
(a standing staff meeting on a calendar). That comes back as
``evidence="not_a_client"`` with no patient and nothing possible, and callers
skip the record rather than ask about it.

Every check but the first needs exactly one candidate. Two patients who share
a name are never guessed between: the answer is ``possible_ids`` and no match,
and the caller asks. Comparisons ignore case and extra whitespace. Deleted
patients are never candidates — not even through a remembered answer.
