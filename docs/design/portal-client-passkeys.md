# Client passkeys for portal sign-in

Status: proposed. Modules touched: `backend/app/portal/` (new
`passkeys.py`, `sms_policy.py`, `in_session_code.py`; changes to `routes.py`,
`recovery.py`, `service.py`, `store.py`, `db_store.py`), the portal
frontend, and the clinician's portal-access card on the chart.

## The problem

Portal sign-in today takes two factors on two channels. The **magic link**
arrives by email and proves the client holds the inbox. The **code** arrives by
text and proves the client holds the phone on the chart (`delivery.py`,
`otp.py`). Redemption needs both (`POST /api/patient/auth/redeem`), and a
session chain lasts at most 30 days (`session_max_lifetime_seconds`). After
that, or on a new browser, the client signs in again and gets another text.

The text code was chosen over a date of birth on purpose (`otp.py`). A date of
birth has little entropy, whoever received a forwarded link often knows it, and
it can't be changed once someone guesses it. An emailed code is no better. It
lands in the same inbox as the link, so the two together prove one thing once.

That makes every sign-in cost one text message. **A deployment may run the
portal without an SMS channel, or with one it wants to use rarely.** Today the
first option means the portal can't be offered at all, and the second isn't
available. This design adds a second factor that costs nothing per use, and a
policy that lets a deployment limit the SMS channel to enrollment and recovery.

## Decision summary

1. **Clients can add a passkey.** A WebAuthn credential, created with user
   verification **required**, held by the client's phone or computer.
2. **Enrollment needs a proven session.** The client enrolls right after a
   sign-in that used the link plus the text code: the existing redemption,
   unchanged. When no text can reach them, the clinician confirms who they are
   during a session and gives them a short code to type instead (the
   *in-session code*, below).
3. **A passkey signs in alone.** Usernameless sign-in from the practice's
   portal page needs no link and no text. On the emailed-link path, a client
   with a passkey uses it as the second step instead of the text.
4. **A deployment can limit the SMS channel to enrollment and recovery**
   (`PORTAL_SMS_POLICY=enrollment_and_recovery`). Every text the step-up path
   sends is counted in a ledger. A per-practice cap and a per-client recovery
   cap bound the total. When a cap is reached, the client is sent to the
   clinician for an in-session code; the text is never silently skipped.
5. **Recovery** after a lost device: another passkey (synced passkeys usually
   come back on a new phone by themselves), else one capped recovery text,
   else the in-session code. Every path is audited.
6. **Credentials are per practice, per client**, in the practice schema, next
   to `companion_sessions`. There is no cross-practice client identity, and this
   design doesn't add one.

## Threat model

These are the attackers the current design already defends against, and
anything new has to hold against them too:

| Attacker holds | Today | With this design |
|---|---|---|
| A forwarded or intercepted invitation email | Can't redeem: no phone | Can't redeem, can't enroll (enrollment needs a redeemed session or an in-session code) |
| The client's inbox, long-term (e.g. a family member) | Can request links, can't get codes | Same. Recovery by passkey or text, never by email alone |
| The client's phone number via SIM swap | Can get codes, but needs the inbox too | Same for the text path. A passkey-only client isn't exposed to it at all |
| A phishing page imitating the portal | Can relay a link and a code in real time | A passkey is bound to the portal's origin and can't be relayed |
| The unlocked shared family device | Has the session in storage until it expires | Has the passkey too, gated by the device unlock. See *Shared devices* |
| A practice account used to send texts to premium numbers | Unbounded | Bounded by the per-practice step-up cap |

The crux is the first row. An invitation link is a bearer credential that
travels by email and can be forwarded. **Nothing reachable with the link alone
may create a credential.** Enrollment therefore requires one of:

- a session minted by a **two-factor redemption** less than 15 minutes ago
  (`chain_started_at` within `passkey_enroll_window_seconds`, default 900), or
- a session already **signed in by a passkey**, to add a second device.

A refreshed session past that window can't enroll. That matches the clinician
side, where adding another passkey needs an MFA-satisfied session
(`PasskeyService.begin_registration`).

## Enrollment

### The ordinary path: one text, then a passkey

1. The clinician sends the invitation. Nothing changes.
2. The client opens the link, asks for a code, receives one text, and redeems.
   Nothing changes up to here.
3. On the first page after redemption, if the browser reports a
   user-verifying platform authenticator
   (`PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()`),
   the portal offers to set up a passkey. The client can skip this. Skipping
   isn't an error, and the offer comes back at the next sign-in.
4. `POST /api/patient/auth/passkeys/register/begin` checks the enrollment
   window and returns creation options. `.../register/finish` verifies the
   attestation, stores the credential, and audits it.

The text in step 2 is the only one a client with a working passkey receives
until they lose access to every device that holds it.

### When the text can't be used: the in-session code

Some clients have no mobile number on the chart, have only a landline, or have
a phone that can't receive texts from a short code. Others belong to a
practice that has used up its step-up cap. For them, **the clinician confirms
identity in person or on a call they are already having**, and that
confirmation becomes the second factor:

1. On the chart's portal-access card, the clinician chooses *Give a sign-in
   code*. The server picks the client's outstanding invitation (or issues one,
   which emails the link as usual), generates a six-digit code, stores its
   peppered hash on that invitation's challenge row with a **10-minute** window
   and `code_source = 'in_session'`, and shows the code on the clinician's
   screen exactly once.
2. The clinician reads it to the client, who opens the emailed link and types
   it where the text code would go. Redemption is the existing route; the
   same attempt cap (5) and the same uniform 401 apply.
3. The client is now in a fresh two-factor session and enrolls as above.

Why this holds. The link proves the inbox, and the clinician proves the
person. Someone who intercepted the link would also need a code the clinician
reads aloud only to a person they recognize, within 10 minutes. The code is
never emailed and never appears in a response to anyone except the clinician
who asked for it. Issuing one is audited with the clinician as the actor.

The clinician sees the code once, can't look it up again, and can issue a new
one (which replaces it). The code doesn't reveal anything clinical, and the
clinician holds a grant on the chart already, so the card requires nothing
beyond `visible_patient`.

The engine has no voice-call delivery. A voice code would cost the same as a
text, and the in-session code covers the landline case at no cost.

## Sign-in

### Is a passkey alone two factors?

Yes, when user verification is required, and this design requires it at both
registration and assertion (`UserVerificationRequirement.REQUIRED`,
`require_user_verification=True`, the same settings the clinician service uses).

- WebAuthn Level 2 §6.2.3, *User Verification*: when the UV flag is set, the
  authenticator has locally checked the user with a biometric or a PIN,
  *something you are* or *something you know*, before it signs. The signature
  itself proves possession of the private key, *something you have*.
- NIST SP 800-63B §5.1.9 classes this as a *multi-factor cryptographic
  authenticator*. The 2024 supplement on syncable authenticators accepts synced
  passkeys at AAL2.
- It is also **phishing-resistant**, which link plus text isn't. The
  signature covers the origin (`clientDataJSON.origin`) and the RP ID hash, so
  a lookalike page gets nothing it can replay.

So a passkey is a stronger sign-in than the link plus text. Requiring the
emailed link on top of it would mean an inbox round trip on every sign-in,
which is the friction passkeys remove, and the inbox adds less than the
passkey already proves. **Recommended: a passkey signs in on its own.**

The assertion's UV flag is checked server-side (`py_webauthn` refuses
`uv=false` when `require_user_verification=True`). A credential registered
without UV is never stored.

### Two ways in

- **From the practice's portal page**: *Sign in with a passkey*. Usernameless
  (discoverable credential). The browser offers whichever credentials it holds
  for this RP ID, and the server finds the client from the `credential_id` in
  the practice schema the host resolves to. Routes:
  `POST /api/portal/practices/{slug}/passkeys/authenticate/begin|finish`.
- **From an emailed link** (an invitation, or a recovery email): the link page
  asks the server what the second step is. A client with an active passkey is
  asked for it (`POST /api/patient/auth/redeem-passkey`, taking the link token
  and an assertion). If the client has no passkey, or chooses *I don't have my
  device*, the request-code path applies, subject to the SMS policy below.

Both mint a session through the existing `PortalAuthService`, so the session
lifetime, the 30-day chain ceiling and revocation are unchanged. When the
chain ceiling arrives, the client signs in with a passkey again, with no text.

### Cross-device

A client on a library computer can sign in with the phone that holds their
passkey (the browser's "use a phone" / hybrid flow, a QR code and Bluetooth
proximity). The engine needs no code for this; the browser handles it.
Nothing is saved on the borrowed computer except the session, which expires.

## The SMS policy

A new deployment setting, `PORTAL_SMS_POLICY`:

- `every_sign_in` (default, today's behavior): the link page offers a passkey
  when the client has one, and a text otherwise, with no counting.
- `enrollment_and_recovery`: a text is sent only when the client has **no
  active passkey** (first sign-in, or a client whose device can't hold one), or
  as a **recovery text** (below). Every step-up text is written to the ledger
  and checked against the caps first.

A deployment may also register a per-practice resolver
(`register_portal_sms_policy(resolver)`, the same registration pattern as the
portal section gate) so the policy and caps can differ by practice. The engine
default reads the settings.

### Caps

All counts come from `portal_step_up_texts` (below). Defaults are engine
settings; a resolver may override them per practice.

| Cap | Default under `enrollment_and_recovery` | Why |
|---|---|---|
| Step-up texts per practice per rolling 30 days | 50 | Bounds toll-fraud and SMS pumping through a practice account that invents charts with premium numbers |
| Recovery texts per client per rolling 30 days | 1 | A lost phone is rare; two in a month is a pattern to look at |
| Recovery texts per client per rolling 365 days | 3 | Same |
| Texts per invitation (existing) | 5 requests/hour | Unchanged |

Destination numbers are also limited to the country codes in
`PORTAL_SMS_ALLOWED_COUNTRY_CODES` (default `1`), which removes the
international-premium-rate pumping case.

**A cap is never a silent skip.** When one is reached, `request-code` returns
409 `PORTAL_TEXT_LIMIT` (distinct from the uniform 401, which is reserved for
refusals of the invitation) and the link page tells the client to ask the
practice for a sign-in code. This leaks nothing to a stranger: the response
comes only after the signature on a real invitation is verified. The refusal
is audited, and the clinician's card shows that the client was refused.

## Recovery: a lost phone or a new device

In order, with the cheapest first:

1. **Another passkey.** iCloud Keychain and Google Password Manager sync
   passkeys, so a replacement phone signed into the same account usually has
   it already. The portal reports `backup_eligible` / `backup_state` at
   enrollment, and the manage page suggests a second device when the passkey
   isn't synced.
2. **A recovery text.** The client asks for a fresh link (the existing
   `POST /api/portal/practices/{slug}/recover`, which is unchanged: uniform 202,
   CAPTCHA, per-address and per-practice windows, active grant only). On the
   link page they choose *I don't have my device*. A text is sent if the
   recovery caps allow. After redeeming, the client is in a fresh two-factor
   session and can enroll the new device.
3. **The in-session code**, when the caps are spent or the phone is gone too.

There are no client backup codes. They're one more secret for a
non-technical client to lose, and the in-session code covers the same case
with a person in the loop.

**What the clinician can do** on the portal-access card:

- See whether the client has a passkey, how many, when each was last used, and
  whether a recovery text was refused (no device names a client typed, no
  AAGUID shown).
- *Give a sign-in code* (the in-session code).
- *Remove sign-in devices*: revokes every passkey for this client.
- *Turn off portal access* (the existing kill switch). It now also revokes
  every passkey along with the sessions and outstanding invitations. Turning
  access back on means a new invitation, and the client enrolls again, so a
  passkey can't outlive the clinician's decision to withdraw access.

**What the client can do** on the portal's sign-in page: list their passkeys
(as "This iPhone, added Oct 3"), remove one, and add another (from a
passkey-signed-in session).

## Shared devices and older ones

**Shared family devices.** A passkey on a shared tablet is guarded only by the
tablet's unlock, and family members often know it. Before enrolling, the portal
asks whether the device is used only by the client, and doesn't create the
passkey if it's shared. The client then signs in with the link plus a text, or
a passkey on their own phone via cross-device. Two family members who are each
clients of the same practice on the same phone get two separate credentials,
and the browser's picker shows both.

The picker shows the RP ID (the portal host) and the account name. **That
reveals that someone on this device is a client of this practice** to anyone
who opens the device's password list. The account name is therefore the
client's email address, never a name or the word "therapy", and the enrollment
screen says plainly what will appear. The host is the practice's own, and
nothing can be done about it.

**Older devices.** Passkeys need iOS 16, Android 9 with current Play Services,
macOS 13, or Windows 10 with Windows Hello. Older browsers report no platform
authenticator, so the portal doesn't offer enrollment. Those clients keep the
link plus a text each time. Under `enrollment_and_recovery`, those texts count
against the practice's cap like any other, and past it they use the in-session
code. Security keys (roaming authenticators with a PIN) also work and need no
special handling.

**Practice hosts.** A passkey is bound to the RP ID it was created for. The
portal's RP ID is the host the portal is served on (the practice's own host,
its hosted portal address, or the shared app host), stored with each
credential. If a practice moves its portal to a new primary host, passkeys
created on the old host don't work there. The old host can list the new one in
`/.well-known/webauthn` (WebAuthn Level 3 *related origin requests*), where the
browser supports it. Otherwise clients fall back to the link and enroll again,
which uses the step-up path. A host move should be rare, and the domain
settings screen should say that clients may need to set up sign-in again.

## Copy

Drafts per `docs/reference/copy-style.md`: second person, no machinery, no
gendered pronouns. The reasoning stays in this doc.

| Where | Draft |
|---|---|
| Offer after first sign-in | **Sign in faster next time.** Use your face, fingerprint or phone passcode instead of waiting for a text. |
| Offer buttons | Set it up · Not now |
| Shared-device question | Is this device used only by you? · Yes, only me · Others use it too |
| Shared-device answer | We won't save sign-in on this device. You can set it up on your own phone instead. |
| Before creating | Your device will save a sign-in for {practice name}. It may appear in your saved passwords under your email address. |
| Done | You're set. Next time, choose **Sign in with a passkey**. |
| Portal page button | Sign in with a passkey |
| Link page, client has a passkey | Confirm it's you with your face, fingerprint or phone passcode. |
| Link page, fallback link | I don't have my device |
| Text limit reached (409) | We can't send a text right now. Ask {practice name} for a sign-in code at your next session or by phone. |
| Entering an in-session code | Enter the code {practice name} gave you. |
| Manage list item | {device}, added {date} · Remove |
| Clinician card button | Give a sign-in code |
| Clinician code display | Read this code to your client. It works for 10 minutes: **{code}** |
| Clinician card, refused text | Couldn't send {client}'s sign-in text on {date}. Give them a sign-in code. |

"Passkey" appears only on the button and the done screen, where the browser's
own prompt uses the same word. Everywhere else the copy says what the client
does: face, fingerprint, passcode.

## Data

All new tables are **per-practice** (in the practice schema, like
`companion_sessions` and `companion_auth_challenges`), created by an OSS tenant
migration and present in the tenant template. Primary keys aren't named `id`;
the RLS posture matches the two companion tables, and the build bead verifies
it against `enable_rls_on_schema` before the migration lands.

**`portal_passkey_credentials`**

| Column | Type | Note |
|---|---|---|
| `credential_id` | text PK | base64url |
| `patient_id` | uuid, indexed | |
| `user_handle` | bytea, unique | 32 random bytes per client; never the patient id, which would put a chart identifier on the device |
| `rp_id` | text | host at enrollment |
| `public_key` | bytea | |
| `sign_count` | bigint | clone check, as for clinicians |
| `transports` | text[] | |
| `aaguid` | text | stored for clone triage and for "This iPhone"; never shown as such |
| `backup_eligible`, `backup_state` | boolean | |
| `enrolled_via` | text | `sms`, `in_session`, `recovery_sms`, `passkey` |
| `created_at`, `last_used_at`, `revoked_at` | timestamptz | soft revoke |
| `revoked_by` | text | `client`, `clinician`, `access_withdrawn` |

**`portal_passkey_challenges`**: `challenge_hash` (SHA-256, PK), `ceremony`
(`register` / `authenticate` / `link_step_up`), `patient_id` (nullable for
usernameless), `expires_at` (300 s), `consumed_at`. Same single-use pattern as
`passkey_challenges`, but in the practice schema so a client ceremony can never
be consumed by the clinician path, or the other way round.

**`portal_step_up_texts`**: the ledger the caps read. `text_id` uuid PK,
`patient_id`, `purpose` (`sign_in`, `enrollment`, `recovery`), `sent_at`. No
number, no code, no message.

**`companion_auth_challenges`**: one new nullable column, `code_source`
(`sms` / `in_session`), so an audit row and the clinician card can tell the two
apart. The in-session code otherwise uses the existing `set_code`.

### Audit events

New `AuditAction` values, each written through `AuditService` with the patient
as the resource:

- `PATIENT_PORTAL_PASSKEY_ENROLLED` (actor: patient; `enrolled_via`)
- `PATIENT_PORTAL_PASSKEY_REMOVED` (actor: patient or clinician; `revoked_by`)
- `PATIENT_PORTAL_PASSKEY_SIGNED_IN` (actor: patient). The existing
  `PATIENT_PORTAL_SESSION_REDEEMED` stays for link redemptions and gains a
  `second_factor` detail (`sms`, `in_session`, `passkey`)
- `PATIENT_PORTAL_SIGN_IN_CODE_GIVEN` (actor: clinician). Never the code
- `PATIENT_PORTAL_RECOVERY_TEXT_SENT` / `PATIENT_PORTAL_TEXT_REFUSED`
  (`cap`: `practice`, `recovery_30d`, `recovery_365d`, `country`)

A suspected clone (sign count went backwards) refuses the assertion and logs
the `credential_id`, as the clinician path does. Nothing logs a phone number,
an email address, a code, or an assertion.

### Rate limits

| Route | Window |
|---|---|
| Passkey authenticate begin/finish | per IP (configurable like `portal_redeem_ip_rate_per_min`) and 60/min per practice slug |
| Assertion failures per credential | 10/hour, then the credential refuses until the hour passes |
| Register begin/finish | 10/hour per session |
| *Give a sign-in code* | 3/hour per client, 30/hour per clinician |
| Link step-up with passkey | the existing per-invitation redeem window (10/hour) |

## What's reused from the clinician passkeys

The clinician implementation (`services/passkey_service.py` and friends)
issues a Firebase custom token carrying `pablo_amr`, and stores credentials in
the platform schema keyed by a Pablo user id. Clients aren't Firebase users and
have no platform identity; their principal is a portal session row in one
practice's schema. So the *service* can't be shared, but most of what's under
it can:

- **Reused as is:** `py_webauthn`; `passkey_ceremony.py`
  (`extract_challenge`, `normalize_aaguid`, `read_transports`); the
  hash-the-challenge, consume-once pattern from `passkey_challenge_store.py`;
  the sign-count clone check, which moves to a shared helper; `webauthn_origins`
  validation; and on the frontend, `@simplewebauthn/browser` with the step
  timeouts and reset from `passkeyCeremony.ts` (PABLO-o6mp), so a stuck
  ceremony in the portal ends the same way.
- **Not reused:** Firebase tokens and `pablo_amr`, backup codes, attestation
  trust roots and the hardware-key floor (clients use `attestation=none`), and
  the platform tables.
- **Different on purpose:** the RP ID is per portal host rather than the single
  `WEBAUTHN_RP_ID`. Origins come from the practice-host resolver
  (`practice_hosts.py`, `hosted.py`) rather than `WEBAUTHN_ORIGINS`, and a
  credential is only ever looked up in the practice schema the host resolves
  to.

**One client, several practices.** A person who is a client of two practices
is two patient rows in two schemas, with two invitations and two sets of
passkeys. Under one shared host the browser shows both, with the same email as
the account name. The engine never joins them, and the usernameless lookup
only searches the practice the page belongs to, so a passkey for practice A
presented on practice B's page is an unknown credential (uniform 401). A
shared family email is already refused by recovery when it matches two charts
in one practice (`recovery.py`). Passkeys don't change that, because each
credential names exactly one client.

## Build slices

| Slice | Contents | Size |
|---|---|---|
| 1. Tables and stores | Tenant migration, template, ORM rows, in-memory and DB stores, RLS registration, `code_source` column | S (1–2 days) |
| 2. Passkey service and routes | Register, usernameless sign-in, link step-up, list/remove, kill switch revokes passkeys, audit, rate limits | M (3–4 days) |
| 3. SMS policy and ledger | `PORTAL_SMS_POLICY`, resolver registration, ledger, caps, country allow-list, 409 refusal, recovery texts | M (2–3 days) |
| 4. In-session code | Clinician route and chart card, redemption with `code_source`, audit | S (2 days) |
| 5. Portal frontend | Enrollment offer, shared-device question, sign-in button, link-page choice, manage list, copy | M (3–4 days) |
| 6. Local-stack e2e | Chromium virtual authenticator: enroll after link plus text, sign in with no text, cap refusal into in-session code, kill switch revokes | S (1–2 days) |

Slices 1, 2 and 5 deliver passkeys under `every_sign_in`. Slices 3 and 4 make
`enrollment_and_recovery` safe to turn on, and the policy shouldn't be offered
until both have landed.

## Texts saved

Today, each session chain costs one text: a new browser, cleared storage, or
the 30-day ceiling. A client who uses the portal about monthly receives about
twelve texts a year. With a passkey on a phone, that falls to the enrollment
text plus the occasional recovery: about one or two a year, roughly a 90%
reduction, before counting clients whose passkeys sync to a new phone.
Under `enrollment_and_recovery`, the remaining texts are bounded per practice
by the cap.
