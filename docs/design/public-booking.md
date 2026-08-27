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
4. **Audit within the owner's scope, as an anonymous actor.** The
   booking POST writes `PATIENT_CREATED` (when a new patient record is
   created) and `APPOINTMENT_CREATED` through the tenant `AuditService`
   with `actor_type: anonymous`. `user_id` stays the owner — that is
   the RLS context the write runs under, and it is what puts the row in
   the owner's own trail, which is where a clinician looks to work out
   where a chart came from. Who acted is answered by the request's IP
   plus `changes`, which carries `source: public_booking` and the
   booking link's id and slug; never the booker's name or email. The
   GET endpoints disclose no PHI and are classified non-PHI in the
   audit guardrail.

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
against live charts, email confirmation must land**, with two layers
that fail safe independently:

1. **Per-link `require_email_confirmation`, born true, no UI.** A
   column on `booking_links` whose `true` default is enforced at the
   database layer on insert, and which is *deliberately absent* from
   the management API schemas and any settings surface. Every link
   ever created requires confirmation; relaxing a specific link is a
   conscious per-deployment operator action (direct update by whoever
   runs the database), not a preference a clinician can flip while
   chasing booking conversion. When required, the booking POST places
   a short-TTL hold on the slot (rate-limited, capped per IP) and
   sends the confirmation link via the email seam; the appointment
   finalizes on click. The `none` email backend refuses to arm this
   path rather than pretend (see "The email seam").

   *Hold semantics.* A hold **is** an appointment row with status
   `pending_confirmation`, paired with a quarantined `pending`
   patient record (invisible in chart lists). Because the slot engine
   already treats every non-cancelled appointment as busy, a hold
   participates in ALL scheduling rules with no engine change: the
   slot leaves everyone else's list, buffers apply around it, and it
   counts toward max-per-day. TTL is ~15 minutes: expiry flips the
   row to cancelled (releasing the slot through the same path a
   cancellation does) and sweeps the pending patient; confirm flips
   it to confirmed, promotes the patient record, and triggers
   calendar sync. An expired-hold click is not a dead end — if the
   slot is still free, finalize anyway; only if someone else took it
   does the booker see "pick another time." Spam holds consuming the
   daily cap are bounded by the per-IP hold cap, the TTL, and the
   CAPTCHA rung at multi-practice rollout.

   *The write race.* Two submissions for the same slot in the same
   instant beat any re-validate-then-insert sequence (a window that
   exists, small, in phase 1 today). The backstop is a partial unique
   index on `(user_id, start_at)` over non-cancelled appointments:
   public bookings are grid-aligned to the link's duration, so the
   same-slot race is always an exact-start collision — the loser's
   insert fails and surfaces as the same 409 "just taken" response.
   Ships with the confirmation work at the latest.

2. **The chart invariant, independent of the flag.** An unverified
   email never attaches a booking to an existing chart — a matched
   email either completes confirmation or lands as a pending record
   the clinician reconciles (the EHR-import client-resolution shape)
   before it touches the chart. This holds even on links where
   confirmation was operator-relaxed: relaxing the flag trades
   "confirm to finalize" for "book instantly as a fresh or pending
   record" — it can never reopen spoofed writes onto a real chart.
   A `personal`-edition practice, which declares it holds no clinical
   charts, is the one place attach-by-match may skip verification —
   a rule keyed to a declared fact, not a toggle.

Until those land, the design intent stands: booking links book *time*,
and chart identity stays therapist-confirmed. Phase 2 is tracked as
epic PABLO-e3a.

### The wound-down practice

A third question, separate from email: **may this practice still
accept writes at all?**

Every authed route sits behind `require_active_subscription`, which
resolves the *caller's* practice and refuses write-intent routes under
`READ_ONLY`. The public surface has no caller, so that gate cannot
apply — and without a replacement, a practice wound down to read-only
would keep accumulating new charts and appointments through a link it
can no longer service, while its own clinician could not create the
same appointment in-app.

`_require_owner_may_accept_bookings` closes that: it resolves the
*link owner's* access level and refuses the booking POST unless it is
`FULL`. Reads stay open — the card and slots still serve, matching how
read-intent routes behave everywhere else — so a lapsed practice's
published URL does not go dark, it stops taking bookings. The refusal
names no billing state; to a booker it is indistinguishable from any
other reason a link stops accepting bookings.

Two deliberate no-ops. `is_core` deployments return immediately —
Pablo Core has no subscription concept to consult. And a build whose
subscription module is absent entirely (a self-host that set
`PABLO_EDITION` on the OSS image, and the OSS test harness) returns
rather than 500-ing every booking: a deployment that bills nobody has
nothing to enforce.

Note this is a **subscription** gate, not a user-state gate. A
deactivated or offboarded clinician whose subscription is still `FULL`
keeps a live booking link; tying link availability to owner account
state is separate work (PABLO-e3a.10).

### Anti-automation ladder

Three rungs, deliberately in this order — each is only worth adding
once the previous one is being beaten:

1. **Per-IP rate limiting (shipped).** Two sliding windows, both
   running before the slug resolves: a loose **browse** window
   (60/minute) covering the card and slots, and a tight **write**
   window (10/hour) on the booking POST, which is the request that
   actually costs something — a patient record and an appointment.

   These are *not* the pre-auth limiter. Sharing that window with
   login and signup was wrong in both directions: browsing the 14 days
   the page shows is ~15 requests and would have exhausted a 10/60s
   login budget, and booking traffic from one NAT'd address would have
   locked out logins for everyone behind it. The two surfaces now hold
   separate budgets.

   Separation lives in the **key namespace**, not in holding distinct
   limiter objects: `RedisSlidingWindow` keys purely on the string it
   is handed, so two limiters passed the same bare IP silently share
   one window. Public booking keys are prefixed
   (`public-booking:` / `public-booking-write:`), and
   `test_rate_limit.py` pins the independence.

   Sufficient for phase-1 scope: low-traffic links, no slot holds to
   exhaust, junk records deletable and rate-bound. Its known limit:
   distributed clients sidestep per-IP windows.
2. **Email confirmation (phase 2, above).** The main bot deterrent
   once links face real traffic: with `require_email_confirmation`
   born true, every finalized booking costs a working inbox and a
   click — more expensive per booking than solving any CAPTCHA, and
   it protects the chart, which a CAPTCHA never does. Unconfirmed
   holds expire in minutes, bounding what a bot can fence off.
3. **CAPTCHA seam.** A `CAPTCHA_PROVIDER` setting (`none` default;
   Cloudflare Turnstile as the first adapter) verified server-side as
   a dependency on the booking POST only — browsing the card and
   slots stays free. The public link card carries the site key so the
   page knows whether to render the widget. The provider sees browser
   signals and a token, never form contents, so nothing PHI-adjacent
   leaves the deployment. The engine defaults to `none` and
   hard-codes no vendor; the *when* is a deployment decision with a
   clear split. A single-practice deployment can reasonably wait for
   evidence of abuse. A deployment hosting many clinical practices
   should enable it at rollout, not after: the operators of such a
   deployment — not the clinicians — hold the knob, waiting means
   some practice's chart list absorbs the first spam wave, and once
   rung 2 is live every bot booking also burns outbound confirmation
   email (sender reputation and spend) even though nothing finalizes.
   Screening before the hold keeps the confirmation machinery clean,
   at the cost of a widget that stays invisible for nearly all real
   bookers.

## What phase 1 deliberately leaves out

- **Email confirmation to the booker.** There is no outbound email
  sender in the engine yet (reminders log today). The confirmation
  screen offers a downloadable `.ics` instead; the clinician-side
  record and calendar sync are authoritative. When an email path
  exists, confirmation + cancel/reschedule tokens hang off it. The
  seam it hangs off is specified in "The email seam" below.
- **Booker-side cancel/reschedule.** Needs signed tokens, which want
  the email path above. Until then the clinician cancels in-app.
- **Timezones.** The scheduling engine treats all times as
  practice-local wall-clock (the `Z` suffix in slot strings is
  cosmetic, matching the rest of the engine). The public page labels
  times as the practice's local time. Real timezone conversion is an
  engine-wide change and out of scope here.
- **Multiple hosts / round-robin, intake forms, payments.** Not
  scheduling-engine concerns yet.
- **Slug reuse after deletion** (PABLO-e3a.5). `delete` is a hard `DELETE`, so a
  released slug is immediately re-registrable by *any* clinician on
  the deployment. A therapist who prints `/book/dr-smith` on a card,
  then deletes the link, hands whoever claims that slug next the
  still-circulating traffic — and with it the names and emails of the
  original practice's clients, collected on a booking form that looks
  legitimate. `RESERVED_SLUGS` guards app routes; nothing guards
  reuse. The fix is a tombstone: soft-delete the row and keep the slug
  claimed. Until then, treat deletion as *deactivation plus a
  released name*, and prefer `is_active: false` for any link that was
  ever published.

## Frontend

`app/book/[slug]/page.tsx` — an unauthenticated page: day picker
(next 14 days), slot grid for the selected day, name/email form,
confirmation card with `.ics` download. Styled with the standard brand
tokens; no dashboard chrome.

Owners manage links through authed CRUD at `/api/booking-links`
(create, list, update copy/duration, activate/deactivate, delete). A
dashboard management surface (Settings → Booking) is the natural next
step; until it lands, links are managed via the API.

## The email seam

Not built; this is the contract for when it lands. It is deliberately
one seam serving three consumers — booking confirmations, the
appointment reminder service (which currently logs where it would
send), and booking-email verification tokens (the security section's
precondition for client self-scheduling) — so the first implementation
pays off three times.

### Protocol

One protocol, one method, in `backend/app/services/email_sender.py`:

```python
@dataclass
class OutboundEmail:
    to: str
    subject: str
    text: str          # plain text first; HTML is a later, optional field
    kind: str          # "booking_confirmation" | "appointment_reminder" | ...

class EmailSender(Protocol):
    def send(self, message: OutboundEmail) -> None: ...
```

`kind` exists for logging and tests, never for provider routing. A
`get_email_sender()` factory mirrors the repository factories and is
injected with `Depends()` like everything else; tests get an
`InMemoryEmailSender` that captures messages, mirroring the in-memory
repositories.

### Backend selection

`EMAIL_BACKEND` in settings picks the implementation:

- **`none` (default)** — logs that a send *would* happen (`kind` only)
  and succeeds. A bare deployment keeps today's behavior and never
  half-works silently; features that *require* delivery (verification
  tokens) must check the backend and refuse to arm rather than pretend.
- **`smtp`** — `smtplib` with STARTTLS, configured per-deployment via
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
  (SecretStr), `SMTP_FROM`. SMTP-first because every provider speaks
  it and the mail relationship — including any compliance agreement it
  needs — stays the deployment's own. A practice's existing workplace
  mailbox is typically already inside its compliance boundary and
  works here unchanged.

Further adapters are per-deployment decisions added behind the same
protocol; the engine never hard-codes a vendor.

### Failure and privacy semantics

- **Sends are best-effort side effects.** A failed confirmation email
  must never fail the booking — same posture as Google Calendar sync:
  log, continue, the in-app record is authoritative. Reminders retry
  naturally on the next scheduler tick (the `reminder_*_sent` flags
  only flip on success). Verification sends are the exception: their
  failure surfaces to the caller, since the flow cannot proceed
  without them.
- **The recipient address is PHI-adjacent** (a patient email on an
  appointment implies a care relationship). Guardrail #5 applies: no
  backend may log the address or body — the `none` backend logs `kind`
  alone, and the `smtp` backend logs success/failure with no
  addressee.
- **Bodies stay minimal**: time, duration, host name, a manage link.
  Nothing clinical, ever — regardless of what agreements the
  deployment's provider has signed.
- Confirmations send from a FastAPI background task after the response
  commits, so a slow provider never holds the booking request (or its
  DB connection) open.

## Future: per-practice domains

Not built; recorded so phase-1 shapes stay compatible with serving
booking pages under a practice's own domain
(`book.sunrisecounseling.example/intro-call`) later.

- **Host → practice resolution.** A platform mapping table (sibling of
  `email_tenant_mappings`) resolves the request's Host header to a
  practice *before* the slug lookup, inside
  `get_public_booking_context` — the single seam every public endpoint
  already passes through. The default host skips the lookup; nothing
  outside that dependency changes.
- **Slug scope.** `booking_links.slug` is globally unique today — the
  strictest constraint, chosen because strict-to-loose is the easy
  migration. When domains namespace links, it relaxes to
  `UNIQUE(practice_id, slug)` via a constraint swap; existing rows
  trivially satisfy the weaker constraint, so no data rewrite. On the
  shared default host, practice-scoped links then need a two-segment
  path (`/book/{practice}/{slug}`).
- **Frontend.** The booker page is chrome-free and serves unchanged
  under any host; middleware rewrites `book.example.com/{slug}` to
  `/book/{slug}`. Per-practice branding is a token layer on the same
  page, not a fork of it.
- **Nothing bakes a domain in.** The confirmation response, audit
  rows, and the appointment note reference the slug only, so links
  survive a later domain move untouched. Certificates and DNS live in
  deployment config, outside this repo.

## Rollout

Off by default (`PUBLIC_BOOKING_ENABLED=false`); a deployment that
wants client self-booking enables the flag and creates links. The
`personal` practice edition works identically, so non-clinical
operators can take bookings (e.g. consultations) with the same
machinery.
