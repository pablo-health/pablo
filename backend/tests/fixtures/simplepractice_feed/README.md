# SimplePractice calendar feed, captured

Two reads of one SimplePractice test account's calendar feed, taken minutes
apart on 2026-09-30 with the account's calendar-sync setting flipped between
them. Same appointments, same UIDs; only `SUMMARY` differs:

- `initials.ics`: the setting that shows initials. Four different clients
  arrive as `J.A. Appointment`.
- `full_names.ics`: the setting that shows full names, typed as typed
  (`jane smith`), a middle initial dropped. One appointment was booked between
  the two reads, so this file has one more event.

Captured, not authored: the parser is tested against what the feed carries,
not against a belief about it. Scrubbed: the per-appointment telehealth URL
token is replaced by one derived from the UID. Everything else is as read.
Clients are made up; there is no PHI.
