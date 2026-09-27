# SimplePractice "Export - Complete" — captured fixture

Captured from a SimplePractice trial account (first archive 2026-09-26, final
archive 2026-09-27). **The PDFs are SimplePractice's own files**, scrubbed in
place with PyMuPDF: each identifying text span was redacted where it sat and
the replacement written back at the same origin and size, and each rewritten
note body was redacted as a block and re-set in the same box. Producer,
fonts, header layout, columns, tables and footers are untouched, which is the
point: a parser has to match the real renderer, not a guess. There are no
text renditions here on purpose: a test reads these PDFs through the same
extractor the importer uses, so what the test proves is the path a real
archive takes. `.vcf` files and the secure-message log are as exported with
the fields below replaced; the two files under `Stored documents` are
stand-ins for opaque uploads.

**Content that changed:** the sample client SimplePractice ships (and the
contact attached to that client) became Lulu Llama and Lola Llama, with new
date of birth, address, phone, email and diagnosis codes, and every note
body, the chart note and the secure message rewritten in our own words. The
clinician is Avery Provider. The two Pablo Bear clients were created for the
capture: their names, birthdays and note text are as entered; their street
addresses, email addresses and the signing IP addresses were replaced.
Amounts, dates of service, billing codes, questionnaire scores, signing
times and the identifiers in file names are as exported.

Re-capturing: `scrub_export.py` beside this file is the tool that produced
the fixture. It takes a mapping file of real value to replacement, which is
deliberately not in this repository, and writes a scrubbed copy of a fresh
export. Re-capture when the export format may have changed (every six months
or so, or on a bug report from a migration), then extract the result and
grep for every real value before committing.

Layout notes: every clinical PDF opens with `Client:` / `DOB:` / `Provider:`,
notes carry an `Appointment:` line (type, date, time range, "Billing code:")
and a `Diagnosis:` column of ICD-10 codes, then a centred title naming the
note type, the body, and a `Created on ... Page N of M` footer. Psychotherapy
notes live in their own top-level folder. Administrative notes (non-clinical
notes about a client) live under `Administrative/<client>/` with a header of
`Client:` and `DOB:` only, no provider or appointment line. Extract these
PDFs in position order (for PyMuPDF, `get_text("text", sort=True)`); raw
content-stream order does not follow the visual layout. Billing PDFs are invoice, statement
and "Statement for Insurance Reimbursement" (superbill). Secure messages are a
plain-text log with `----- <date> -----` day headers and `<Sender> [<time>]`
lines. Client uploads export under `Stored documents/<client>/<n>-<original
filename>`, numbered in upload order (captured from a second export on
2026-09-27; the files here are stand-ins for opaque uploads). The client
roster CSV is a separate SimplePractice export and is not in this archive.

Two clients with the same name (captured 2026-09-27): the Contacts folder
keeps one card per client, distinguished only by the id in the file name and
the email inside, but every other folder is keyed by display name, so both
clients' notes, psychotherapy notes and uploads land in ONE `Pablo Bear`
folder. Nothing inside a note names the client beyond the display name, and
when a client has no date of birth the `DOB:` line is simply absent (see the
Pablo Bear notes in an earlier capture). Both uploads are numbered `1-`.
Attribution of those records to a client is therefore not possible from the
export alone, unless the two clients differ in some field a document carries.

Giving one client a middle initial (captured 2026-09-27, final archive) is
the cheapest way to make them differ: the folders stay `Pablo Bear`, but the
`Client:` line of every note for that client, including notes locked before
the change, renders the current display name `Pablo A. Bear`; the secure
message file is named `<Provider>-Pablo-A.-Bear.txt` and each sender line
reads `Pablo A. Bear`; and the contact card is renamed
`Pablo A. Bear - <id>.vcf`. The card's *contents* do not change: `N` and `FN`
still read `Pablo Bear`, so the middle initial has to be read from the card's
file name. Uploads stay ambiguous either way. **Administrative notes print
first and last name only** (`Client:   Pablo Bear` on the note that belongs to
the client whose other notes say `Pablo A. Bear`), so on that class of
document the name disagrees with the `DOB:` line and the birthday has to
win; a reader that attributes by name alone would mis-file it. A date of birth, when set,
prints as a `DOB:` line on notes and as `BDAY` on the card and separates
clients the same way.

The Pablo Bear progress note also shows the locked-and-signed variant: a
`Provider` block with the signer, the signing time and an IP address, and a
final `Locked and Signed by ...` footer line.
