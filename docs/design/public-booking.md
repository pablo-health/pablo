# Public Booking Links

**Status:** Phase 1 implemented (flag-gated, default off)
**Flag:** `PUBLIC_BOOKING_ENABLED`

## Problem

The scheduling engine already computes availability (working hours,
buffers, blocked days, per-day caps) and manages appointments, but every
route sits behind clinician auth. Getting a first appointment on the
calendar still requires the clinician to do the data entry themselves:
there is no way for a prospective or existing client to pick a time
directly. Third-party schedulers solve this, at the cost of a second
source of truth for appointments, a separate availability configuration,
and another vendor holding client names and appointment times.

## Solution

A **booking link** is a clinician-created, publicly reachable slug
(`/book/{slug}`) that exposes a deliberately narrow surface:

1. `GET  /api/public/booking-links/{slug}` — the link's display card
   (host name, event title, description, duration). Nothing else.
2. `GET  /api/public/booking-links/{slug}/slots?date=YYYY-MM-DD` — free
   slots for one date, computed by the existing `AvailabilityEngine`
   against the owner's rules and appointments.
3. `POST /api/public/booking-links/{slug}/bookings` — book a slot with a
   name and email. Creates (or reuses, matched by email) a patient
   record and a confirmed appointment via the existing repositories, so
   the booking lands on the clinician's Pablo calendar — and, when
   connected, their Google Calendar — like any other appointment.

Nothing is readable back out: the public surface never returns patient
data, existing appointments, or anything beyond "these times are open."

## Data model

`platform.booking_links` — a **platform** table, not a per-tenant one,
because slug resolution must happen *before* a tenant schema can be
selected. It stores no PHI: slug, owner, display copy, duration.

| column           | type         | notes                                        |
|------------------|--------------|----------------------------------------------|
| id               | UUID PK      |                                              |
| slug             | VARCHAR(64)  | unique, `^[a-z0-9][a-z0-9-]{2,63}$`, reserved names refused |
| user_id          | UUID         | FK `platform.users`, the link's owner        |
| practice_id      | VARCHAR(128) | FK `platform.practices`; NULL in single-schema deployments |
| host_name        | VARCHAR(255) | public display name, set explicitly by the owner |
| title            | VARCHAR(255) | e.g. "Intro call", "Initial consultation"    |
| description      | TEXT NULL    | shown on the public page                     |
| duration_minutes | INTEGER      | 5–480                                        |
| session_type     | VARCHAR(20)  | `individual` (default) / `couples` / `group` |
| is_active        | BOOLEAN      | inactive links 404 publicly but stay listed for the owner |
| created_at / updated_at | TIMESTAMPTZ |                                       |

## Request flow (public)

Every public endpoint goes through one dependency chain:

1. **Rate limit** by client IP (`require_rate_limit`, the same pre-auth
   limiter the passkey endpoints use).
2. **Resolve slug** in `platform.booking_links`; missing *or inactive*
   links are an identical 404 (no oracle for "exists but off").
3. **Enter the tenant.** When multi-tenancy is on, the link's
   `practice_id` resolves to the practice schema and
   `set_tenant_schema()` scopes the session; single-schema deployments
   skip this. Either way `arm_current_user_id(owner)` arms RLS, so
   every repository call behaves exactly as if the owner were making
   it — no RLS bypass, no special-cased queries.
4. **Audit as the owner.** The booking POST writes
   `PATIENT_CREATED` (when a new patient record is created) and
   `APPOINTMENT_CREATED` through the tenant `AuditService` under the
   owner's identity, with a `source: public_booking` marker in the
   changes payload. The GET endpoints disclose no PHI and are
   classified non-PHI in the audit guardrail.

### Booking validation

The POST re-derives free slots server-side for the requested date and
accepts only a `start_at` that exactly matches an open slot — the client
is never trusted about availability. Bookings are refused more than
60 days out or in the past. Patient reuse is by exact-email match among
the owner's patients; anything else creates a fresh record.

## Security posture — the email-reuse question

The booking POST reuses an existing patient record when the booker's
email exactly matches one of the owner's live patients. Two distinct
questions hide in that design, with different answers:

**Does it create an existence oracle?** No — by construction, and
pinned by `test_booking_reveals_nothing_about_existing_patients`. The
confirmation carries only link-derived fields (host, title, times,
duration); status code, shape, and values are identical whether the
email matched an existing chart or created a fresh record, and the
reuse path never writes booker-supplied names onto the existing chart.
An anonymous caller cannot learn from any response whether an email
belongs to a patient. (A timing side channel between the two paths
exists in principle — the create path does more writes — but it is
noise-dominated over the network and further blunted by the IP rate
limit.)

**Can an unverified email attach writes to a real chart?** Yes, and
this is the real limit of phase 1: anyone who knows a patient's email
can create an appointment (with attacker-chosen note text) that lands
on that patient's chart and the clinician's calendar, attributed to the
real client. Nothing is disclosed to the attacker — it is an integrity
problem, not a confidentiality one — but a clinician could mistake the
spoofed booking for a genuine one. The same unverified-email property
also allows slow-drip junk patient records (rate-limited, deletable,
but real).

This is acceptable while a deployment uses booking links for
non-clinical intake — new-contact consultations, `personal`-edition
practices — which is exactly the phase-1 scope the default-off flag
protects. **Before positioning booking links as client self-scheduling
against live charts, email verification must land**: either a
confirmation link proves control of the address before the booking is
attached (the natural companion to the outbound-email work below), or
public bookings enter a pending state the clinician reconciles before
they touch a chart — the same shape as the EHR import client-resolution
flow. Until one of those exists, the design intent is: booking links
book *time*, and chart identity stays therapist-confirmed.

## What phase 1 deliberately leaves out

- **Email confirmation to the booker.** There is no outbound email
  sender in the engine yet (reminders log today). The confirmation
  screen offers a downloadable `.ics` instead; the clinician-side
  record and calendar sync are authoritative. When an email path
  exists, confirmation + cancel/reschedule tokens hang off it.
- **Booker-side cancel/reschedule.** Needs signed tokens, which want
  the email path above. Until then the clinician cancels in-app.
- **Timezones.** The scheduling engine treats all times as
  practice-local wall-clock (the `Z` suffix in slot strings is
  cosmetic, matching the rest of the engine). The public page labels
  times as the practice's local time. Real timezone conversion is an
  engine-wide change and out of scope here.
- **Multiple hosts / round-robin, intake forms, payments.** Not
  scheduling-engine concerns yet.

## Frontend

`app/book/[slug]/page.tsx` — an unauthenticated page: day picker
(next 14 days), slot grid for the selected day, name/email form,
confirmation card with `.ics` download. Styled with the standard brand
tokens; no dashboard chrome.

Owners manage links through authed CRUD at `/api/booking-links`
(create, list, update copy/duration, activate/deactivate, delete). A
dashboard management surface (Settings → Booking) is the natural next
step; until it lands, links are managed via the API.

## Rollout

Off by default (`PUBLIC_BOOKING_ENABLED=false`); a deployment that
wants client self-booking enables the flag and creates links. The
`personal` practice edition works identically, so non-clinical
operators can take bookings (e.g. consultations) with the same
machinery.
