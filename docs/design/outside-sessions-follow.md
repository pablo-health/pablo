# Following the sessions another service puts on a calendar

Module: `backend/app/services/outside_sessions.py`

Follow the sessions another service puts on a calendar Pablo reads.

Many clinicians already have their sessions put on their main calendar by
another scheduling service, or published in a calendar feed. Pablo follows
them: each one that looks like a session is held as an open row
(``external_calendar_events``) and asked about — "is this a client? which
one?" — and the answer is remembered through ``app.patients.matching``, so
the next event for the same client becomes an appointment without asking,
when the event can say which client it is.

**When a remembered answer books without asking** is ``unattended``, one rule
for every source. An identifier books on its own only while it can't have
been reused by someone else, and only onto an active chart:

* a provider's own series id (``series:``), while the series still carries
  the title it was answered under: editing every event of a Google series to
  another client's name keeps the id, so a changed title asks again;
* a feed's own client code (Sessions Health's ``SH00001``);
* a feed title that is a full name, when exactly one of the clinician's own
  charts bears it (middle names aside). A name two charts share, or a new
  client with an identical name, can't be told apart by the title;
* a calendar event's title that is such a full name, by the same rule: the
  whole title, a whole piece of it ("Jane Smith - Therapy"), or the name after
  a session word ("Session with Jane Smith", not "Lunch with Jane Smith"), for
  an event that hasn't started, when nothing else the title could be read as
  points at anyone else. A series booked this way is remembered under the
  title it was booked from, so a series retitled to someone else asks again.

The two name rules apply only for a clinician who lets a name book
(``UserPreferences.books_sessions_named_in_title``); otherwise such a session
is asked about with the client filled in. Remembered answers book either way.

Never: a remembered slot (``shape:``, Monday 10:00 "Session" may be someone
else a year on), initials ("J.A." fits four clients on one real feed), or any chart that
is inactive or on hold — a session for an inactive client is the cue to make
them active again, which the question offers. Whatever is remembered is the
question's pre-fill: one tap to confirm or change. Initials and shared names
are asked about once per event, since each event could be a different
client; everything else is asked once per identifier.

What becomes a row is deliberately narrow, because a dentist appointment must
never ask "is this a client?". An event is held when it repeats, when the
matcher finds a patient it could be (initials, a shortened name or a shared
name are a question here, not an answer), or when its identifier is already
remembered as a client. Anything else stays a busy block. An identifier
remembered as not a client never becomes a row.

**Whose answer it is.** A feed's client code or name is the clinician's own:
a Sessions Health code is numbered from their export, and two clinicians'
clients can share a name. A calendar's series is that calendar's, keyed by the
calendar the row was read from (``answer_scope``); a personal calendar has
one follower, and a shared calendar's answer is shared. A row from before
calendars were recorded came from the main calendar, so it falls back to
the main calendar the caller says it knows (``main_calendar_id``).

An open row is not an appointment: ``appointments.patient_id`` stays required,
and nothing here writes an appointment without one. **One outside event is
at most one live appointment in the practice**: an answer that finds the
event already booked — by a colleague following the same calendar, or by a
request that got there first — links its row to that appointment instead of
making another; the database's unique index is the race guard. An answered
row's appointment follows its event — see ``google_calendar_follow`` for the
guards on moves and deletions. Nothing here ever writes to the calendar the
event came from.

HIPAA: event titles often carry a client's name. They stay on the row and the
chart they are confirmed to; the title an answer was given under is kept as a
keyed digest; logs carry counts only.
