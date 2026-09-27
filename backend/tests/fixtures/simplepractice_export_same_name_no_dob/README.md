# Same name, no birthday — captured fixture

A subset of an earlier SimplePractice export of the same trial account
(captured 2026-09-27, before either client had a date of birth or a middle
initial), scrubbed with the same tool and mapping as `../simplepractice_export`.
It exists for one case: two clients named Pablo Bear whose records cannot be
told apart from the archive at all.

- `Contacts/` holds one card per client, distinguished only by the id in the
  file name and the email inside; neither has `BDAY`.
- `Medical Records/Pablo Bear/` and `Psychotherapy Notes/Pablo Bear/` hold the
  one visit's two notes; their headers read `Client:` then `Provider:` with no
  `DOB:` line at all, which is how the export renders a client without a
  birthday. The progress note is locked and signed.
- `Stored documents/Pablo Bear/` holds two stand-ins numbered `1-`, one upload
  from each client.

A reader given this folder must put every one of these records in front of
the practice to assign; nothing here lets it decide.
