# SimplePractice web calendar feed, captured

The SimplePractice web calendar loads its appointments as JSON from
`GET /frontend/appointments` (JSON:API, the clients included). A practice can
save those responses as files under a `calendar/` folder and import the
folder; this fixture is what such a folder holds.

**Captured 2026-10-20** from one SimplePractice test account's web calendar:
`appointments-01.json` through `appointments-07.json` are the responses as
the browser received them while the calendar was paged back and forth, with
names replaced, ids kept and offsets kept. Five of the seven are committed
here (`01`, `02`, `05`, `06`, `07`); the two left out were empty windows
identical in shape to `02`. The windows overlap: appointment `3793472377`
appears in `01`, `05` and `06`, and the later file carries a different copy
(`hasNote` and `isRecurring` moved), which is what the reader's
latest-file-wins rule is tested against. `07` holds two appointments for two
clients. In this capture the included client entries carry only `isMinor`;
the client's display name arrives as the appointment's `title`.

**Authored, not captured:** `appointments-edge-group-external.json` was
written by hand in the same envelope shape, because the capture held no
`groupAppointments` or `externalAppointments` entries and no client with the
fuller attribute set. It adds: a client entry with `firstName`, `lastName`,
`preferredName`, `hashedId`, `enhancedDataProtection: "false"` and an empty
`revokedAdminIds`; an appointment after the 2026-11-01 end of daylight time,
so its offset is `-05:00` while the captured ones are `-04:00`; a past
`No Show` and a past `Cancelled` appointment; one `groupAppointments` entry
(`clientsCount`); and one `externalAppointments` entry (`title`, `eventType`,
`occurrences`). Being authored, it proves only what the reader does with
those shapes, never that the feed emits them that way; re-capture before
trusting a parser change against it.

Booleans in the feed are the strings `"true"` and `"false"`. Window
boundaries carry the practice's offset, which differs across a daylight-time
change. The same appointment id recurs in several files when the practice
pages back and forth; the copy from the latest file (file names sort in
capture order) is the one that counts.
