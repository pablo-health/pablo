# Telehealth: where a session happens online

Pablo hosts no video. A practice already has a room — a Google Meet through
the calendar it already keeps, a Zoom account it already pays for, a doxy.me
waiting room its clients already recognise — and Pablo's job is to ask that
room for a link, put it on the appointment, and show it to the right person
at the right moment.

That is a deliberate limit rather than a gap. Video is a service somebody has
to run, be liable for and sign a business-associate agreement over, and a
practice that has already chosen one does not need a second. It also keeps
the media boundary simple: nothing here retrieves a recording from any
vendor. Session audio is captured on the clinician's own machine, inside
Pablo's boundary, identically whichever room the session was held in.

## The seam

`app.services.telehealth` holds the contract and nothing vendor-shaped:

- `MeetingProvider` — `is_connected`, `create_for_appointment`, `cancel`.
- `MeetingLink` — the URL, which provider issued it, and the vendor's own
  handle for it when there is one.
- `MeetingProviderRegistry` — who can be asked, narrowed twice: by what the
  deployment offers, and by what this clinician has actually connected.
- The join window, computed in one place so the portal, the diary and a
  reminder cannot come to different conclusions.

The adapters live in `app.meeting_providers`. Each keeps its own API paths,
URL parameters and OAuth scopes to itself.

## Which room an appointment gets

In order:

1. **A link the clinician typed.** It wins outright, whatever any preference
   says, and is recorded as the `manual` provider. It is the escape hatch for
   every service Pablo has never heard of, and what a clinician reaches for
   when a vendor is having a bad morning.
2. **What the booking asked for**, if the clinician has connected it.
3. **The clinician's own default**, from their session defaults.

None of the above means an in-person appointment, which is the ordinary case
and must never acquire a room by accident.

A provider that cannot produce a room never fails the booking. Somebody is
expected at a time; the missing piece is a link, and a clinician can paste
one. Same judgement the calendar push already makes.

## The providers

| Provider | How a room is made | What is stored |
|---|---|---|
| `google_meet` | The calendar event asks Google for a conference (`conferenceData.createRequest`, written with `conferenceDataVersion=1`) and the link is read back off the event. Offered only to a clinician whose calendar is connected. | The link, on `video_link`. |
| `zoom` | `POST /users/me/meetings` in the clinician's own account, one meeting per appointment. Waiting room on, join-before-host off, meeting authentication off, and no patient name in the topic. | The join URL, and the meeting id on `meeting_external_id`. |
| `doxy_me` | The clinician's permanent room URL, with `?username=…&autocheckin=true&pid=…` when the practice's plan has the Clinic check-in features. | The composed URL, and the opaque handle on `meeting_external_id`. |
| `manual` | The clinician pastes one. | Whatever they pasted. |

A Zoom meeting is deleted when the appointment is cancelled, and patched when
it moves. A Meet conference goes when its calendar event does. A doxy.me room
is permanent, so there is nothing to release.

### The handle in a doxy.me URL

`pid` is an HMAC over the appointment id under a key derived from the
deployment's own secret, truncated to 96 bits (`app.meeting_providers.pid`).
It is never the appointment id and never a patient id: the URL is mailed to
people, sits in their browser history and lands in the vendor's access log,
so what it carries has to be a value that identifies the visit to us and
nothing to anybody else.

## The join window

A link is offered from `TELEHEALTH_JOIN_WINDOW_BEFORE_MINUTES` before the
start until the scheduled end, and never on an appointment that was
cancelled. The window reaches the portal with the rest of the practice's
booking policy, so a deployment that opens its rooms an hour early opens them
an hour early everywhere.

Outside the window a video appointment says the link will appear shortly
before the appointment. A row with neither a button nor a sentence is
indistinguishable from an in-person visit.

The clinician's own Start action is not window-bound: opening the room early
is how the room is ready when somebody arrives.

## Reminders

A reminder carries the time and the practice's name. The join link is off by
default and `TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS` is the whole gate —
`ReminderService.reminder_fields` applies it once, so two dispatchers cannot
answer it differently. The link is not neutral: it names the video service,
and it tells whoever is holding the phone that this person has an
appointment.

## The waiting-room webhook

`POST /api/webhooks/telehealth/room` records that a patient checked in, and
that the call started and ended. It is **off unless
`TELEHEALTH_DOXY_WEBHOOK_SECRET` is set** — with no secret there is nothing
to verify a delivery with, so the route answers 404 rather than 401.

The secret is compared in constant time before the body is parsed. Each event
writes one timestamp column on the appointment, and only when that column is
still empty, so a redelivery finds it set and changes nothing. That is the
whole of the dedupe: there is no second ledger to keep in step. A handle
matching no appointment is `200` — rooms are used for things that are not
Pablo appointments.

## Deployment settings

| Setting | Default | What it does |
|---|---|---|
| `TELEHEALTH_PROVIDERS_ENABLED` | `manual` | Which providers this deployment offers. Comma-separated. |
| `TELEHEALTH_JOIN_WINDOW_BEFORE_MINUTES` | `15` | How long before the start a link is offered. |
| `TELEHEALTH_INCLUDE_JOIN_LINK_IN_REMINDERS` | `false` | Whether a reminder carries the link. |
| `TELEHEALTH_DOXY_CLINIC_FEATURES` | `false` | Compose room URLs with the check-in parameters. |
| `TELEHEALTH_DOXY_WEBHOOK_SECRET` | *(empty)* | Shared secret for the waiting-room webhook. Empty disables the route. |
| `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` | *(empty)* | The deployment's own Zoom OAuth app. Without a client id, Zoom is not offered. |

The Zoom OAuth app's redirect URI is checked against the same list every
other connect flow uses (`app.auth.oauth_redirect`): this deployment's own
frontends, `localhost` for development, and the desktop app's schemes.

## What is not here

No video SDK, no embedded player, no recording retrieval from any vendor, and
no vendor business-associate agreements — those are the practice's, with the
service the practice chose.
