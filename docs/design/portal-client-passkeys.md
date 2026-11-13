# Portal sign-in without a text at every visit: client passkeys

Status: **chosen** (2026-11-13). This is the design being built. The
alternative, [`portal-client-sign-in.md`](portal-client-sign-in.md) (the link
plus a password), stays in the repo as the path not taken.

**Why this one: it is the more secure path.** A passkey is phishing-resistant
and there is nothing for a client to reuse from another site; a password is
weaker on both counts. It also keeps a forgotten credential from becoming
work for the clinician, because a passkey usually follows the client to a new
phone. The cost is a few more things for a client to understand, and the
synced-account exposure described under *Decision
summary*, which the known-browser check, the new-device notice and protected
mode are there to answer. That trade was accepted.

Both documents solve the same problem. Read that one's *The problem*
first; this one doesn't repeat it. Shared pieces (the SMS policy ledger, the
in-session code, the country allow-list) are specified once there and only
summarized here.

Modules touched: `backend/app/portal/` (new `passkeys.py`, `known_browsers.py`,
`sign_in_approvals.py`, `protected_mode.py`, `sms_policy.py`,
`in_session_code.py`), the portal frontend, the clinician's portal-access card,
and the notice channel (two new notice names).

## Decision summary

1. **First sign-in proves identity the way it does today.** The link plus the
   one text code, or the link plus the clinician's in-session code. The client
   then adds a passkey within 15 minutes of that sign-in. Nothing reachable
   with a forwarded link alone can create a credential.
2. **A passkey signs in on its own from a browser the portal already knows.**
   User verification is required (device plus face, fingerprint or PIN), which
   makes it two factors and phishing-resistant.
3. **A browser the portal hasn't seen needs a second step**: approval from a
   known browser (the default, at no cost), else a capped text code, else the
   emailed link. The client gets an email that a new device signed in, with a
   *This wasn't me* link, and the clinician's card shows it.
4. **Protected mode, set per client by the clinician**: passkeys are added
   only in session, new browsers are refused (adding one happens only in
   session), and a deployment may require a device-bound credential.
5. **The floor is today's flow.** A device that can't hold a passkey, or a
   client without their device, falls back to the link plus a text (capped),
   then the in-session code. No path is weaker than link plus text.
6. **The RP ID is the portal host. The account name is the client's email.**
   The client is told plainly what their device will show before the passkey
   is saved.

## Why the extra machinery: synced passkeys

A passkey on an iPhone or Android phone is normally synced through iCloud
Keychain or Google Password Manager. A synced passkey appears on every device
signed into that account. For most clients that's the convenience they want.
For a client whose partner or parent shares that account, or knows its
password, it means the person they may be protecting themselves from can sign
in to their therapy portal from the partner's own device.

User verification doesn't help here, because the partner unlocks their own
device. The design answers with three layers, each covering what the one
before misses:

| Layer | Stops | Doesn't stop |
|---|---|---|
| Known-browser check plus approval | A synced passkey used from a device the client never signed in on | A device the client did sign in on (a shared family tablet) |
| New-device email and the devices list | Silent access: the client finds out and can revoke it | Anyone who also controls the client's inbox |
| Protected mode | Every new browser, whatever the attacker holds, and synced credentials where required | A stolen, unlocked device the client already uses |

## Threat model

| Attacker holds | Link plus text (today) | Passkeys |
|---|---|---|
| A forwarded or intercepted link | Can't redeem: no phone | Can't redeem, can't enroll |
| The client's inbox, long-term | Can request links, can't get codes | Same. The emailed-link step only ever pairs with a passkey |
| The client's number via SIM swap | Needs the inbox too | Gets only a capped new-browser text, and still needs the passkey |
| A phishing page | Can relay link and code in real time | Gets nothing it can replay: the signature covers the origin |
| A partner on the client's synced account | n/a | New browser: needs approval from the client's known browser, a text to the client's phone, or the client's inbox. Notified. Refused outright in protected mode |
| The client's unlocked shared device | Has the session until it expires | Has the passkey, gated by the device unlock. See *Shared devices* |
| The database | Code hashes | Public keys and token hashes: nothing that signs in |

## Enrollment

1. The clinician sends the invitation. Nothing changes.
2. The client opens the link, gets one text, and redeems. Nothing changes up
   to here. If no text can be used, the clinician gives an in-session code
   instead (as specified in `portal-client-sign-in.md`).
3. If the browser reports a user-verifying platform authenticator, the portal
   offers a passkey after asking whether the device is used only by the
   client. On a shared device it doesn't create one.
4. Registration (`POST /api/patient/auth/passkeys/register/begin|finish`)
   requires a session whose `minted_via` is `sms`, `in_session` or
   `passkey_known` (adding another device from a known browser) and whose
   `chain_started_at` is less than 15 minutes ago. Both are defined under
   *How a session was minted* below. UV is required, attestation is `none`, and the user handle is 32 random
   bytes per client (never the patient id).
5. The browser that enrolled becomes the first **known browser**.

Before the passkey is saved, the client sees what their device will keep: an
entry for the practice's portal address under their email. The RP ID is the
portal host, and the account name is the email. Neither is hidden, and the
copy says so plainly.

### How a session was minted

Two rules (registration above, and protected mode below) depend on how a
session began, so the session records it. `companion_sessions` gains one
column, `minted_via`, set when a sign-in mints a new chain:

| `minted_via` | Minted by |
|---|---|
| `sms` | Link plus text code (first sign-in, or the fallback floor) |
| `in_session` | Link plus the clinician's in-session code |
| `passkey_known` | Passkey sign-in from a known browser |
| `passkey_approval` | Passkey from a new browser, approved from a known one |
| `passkey_text` | Passkey from a new browser, plus a text code |
| `passkey_email` | Passkey from a new browser, plus the emailed link |

Mint time is the existing `chain_started_at`: it is set when the chain is
redeemed and never moves, so no second column is needed. A refresh rotates
`jti` and `issued_at` and copies `minted_via` and `chain_started_at` forward
unchanged, so a refreshed session qualifies for exactly what the original
did. Every sign-in starts a new chain. Sessions minted before the column
exists have `minted_via` null, and null qualifies for neither rule.

## Sign-in

### From a known browser

On the practice's portal page, *Sign in with a passkey* runs a usernameless
ceremony
(`POST /api/portal/practices/{slug}/passkeys/authenticate/begin|finish`). The
server finds the credential in the practice schema the host resolves to,
verifies the signature with UV, checks the sign count, then checks the
known-browser cookie for that client. If it matches, a session is minted
through `PortalAuthService` (same lifetime, same 30-day chain ceiling, same
revocation).

### The known-browser token

At every successful sign-in the server sets a cookie: a 256-bit random value,
`HttpOnly`, `Secure`, `SameSite=Strict`, host-only, with the longest lifetime
browsers allow (400 days in Chromium; Safari may cap script-set storage, which
is why this is a server-set cookie). The server stores only its SHA-256, in
`portal_known_browsers`, one row per client per browser. A shared browser can
therefore be known for two clients independently. Every use updates
`last_seen_at` and the browser family (from the user agent, e.g. "Safari on
iPhone"), which is what the devices list shows.

**What the token can't catch, plainly:**

- **Cleared storage, a new browser profile, or private browsing.** The client
  is treated as a new browser and goes through the second step. That's an
  annoyance, not a hole, but clients who browse privately will see it every
  time.
- **A stolen, unlocked device the client already uses.** The cookie and the
  passkey are both on it. Only revoking (by the client from another device, or
  by the clinician) stops it.
- **Malware on a known device**, which can copy the cookie. The passkey still
  needs the device's UV, but the second step would be skipped elsewhere.

### From a new browser: the second step

A verified passkey assertion from a browser without a matching token doesn't
mint a session. It creates a **pending sign-in** (`portal_sign_in_approvals`,
10-minute expiry) and offers, in order:

1. **Approve from a known browser** (default, at no cost). The client's known
   browsers that have a live session show *Approve this sign-in?* with the
   browser family and the time. A notice email (`sign_in_approval_requested`)
   tells the client to open the portal on a device they've used before. The
   email is only a pointer: approving takes a known browser plus a session
   there, and the email link alone approves nothing.
2. **A text code**, to the number on the chart, if the SMS policy allows it.
   Counted in the ledger with purpose `new_browser`, and subject to the
   practice cap.
3. **The emailed link**, as the last resort. The client asks for a sign-in
   link, and opening it in this browser completes the pending sign-in (the
   passkey plus the inbox). It's last because the inbox is the factor a
   partner is most likely to share.

On completion, the browser gets its known-browser token, and the sign-in is
reported (below). A denial from a known browser cancels the pending sign-in,
reports it to the clinician's card, and offers *This wasn't me*.

### Reporting a new device

Every new-browser sign-in sends a notice (`new_device_signed_in`) to the
client's email: "A new device signed in to your portal", with the browser
family and the time, and a **This wasn't me** link. That link is a signed,
single-use token, valid for 7 days. Opening it does only destructive things:
it revokes that browser's token and sessions, cancels any pending approvals,
and flags the event on the clinician's card with a suggestion to turn on
protected mode. A forwarded *This wasn't me* link can sign a device out and do
nothing else, which is why it needs no second factor.

The notice carries nothing clinical, as with every portal notice
(`delivery.py`).

### The devices list

In the portal, *Your devices* lists each passkey (as "iPhone, added Oct 3") and
each known browser ("Safari on iPhone, last signed in Nov 12"), with *Remove*
on each. Removing a passkey revokes it; removing a browser revokes its token
and sessions. The same list, read-only with a *Remove all* action, is on the
clinician's card.

## Protected mode

A clinician sets it per client on the portal-access card (audited), for a
client who has said someone else may have access to their devices or accounts.
While it's on:

- **Passkeys are added only in session.** Registration requires a session
  with `minted_via = in_session` and `chain_started_at` less than 10 minutes
  ago. A session minted any other way can't add one, however recent.
- **New browsers are refused, on every path.** A browser with no known-browser
  token for this client can't sign in except with an in-session code. A
  passkey assertion from it creates no pending sign-in: no approval, no text,
  no email link. A link-plus-text redemption from it (the fallback floor) is
  refused the same way, after the invite signature verifies and before any
  text is sent or counted. Both answer *Ask {practice name} to add this
  device* and write `PATIENT_PORTAL_NEW_BROWSER_REFUSED`. Adding a browser
  means redeeming the link on it with an in-session code. A known browser
  keeps the fallback floor: link plus text from it still works.
- **Device-bound credentials, if the deployment requires them**
  (`PORTAL_PROTECTED_REQUIRE_DEVICE_BOUND`, default off). Registration refuses
  a credential whose backup-eligible flag is set, so the passkey can't sync.
  In practice that means a hardware security key or a platform that doesn't
  sync (Windows Hello). iPhone and Android passkeys always sync, so the default
  is off, and the new-browser block already stops a synced copy from being used
  anywhere the client hasn't signed in.
- Turning protected mode on asks the clinician which known browsers to keep,
  from the devices list, ideally with the client in session. Every browser not
  kept is revoked with its sessions, and pending sign-ins are cancelled.

## The fallback floor

| Situation | Path |
|---|---|
| First sign-in | Link plus text, or link plus in-session code (today's flow) |
| Device can't hold a passkey (older OS, no platform authenticator) | Link plus text each time, counted against the cap, then the in-session code |
| *I don't have my device* | Same |
| Cap reached | In-session code (409 `PORTAL_TEXT_LIMIT` first, never a silent skip) |

There is no path weaker than link plus text. The emailed link only ever counts
as a factor alongside a passkey. Under protected mode the floor holds only on a
known browser (see *Protected mode*).

## Turning portal access off

The existing kill switch revokes every passkey, every known browser and every
session for the client, and cancels pending sign-ins. If access is turned back
on, the client starts again at *Enrollment*.

## The SMS policy

What slice 5 builds, and only this. `PORTAL_SMS_POLICY` takes two values:

- `every_sign_in` (default): today's flow. A text at every link redemption,
  passkeys not offered, nothing counted.
- `limited`: passkeys offered. A text is sent only for one of three purposes,
  and each is written to `portal_step_up_texts` before sending:
  - `enrollment`: the first sign-in's link plus text.
  - `new_browser`: the second step for a passkey from a new browser.
  - `fallback`: link plus text from a client with no usable passkey (no
    platform authenticator, or *I don't have my device*).

  Every text under `limited` is checked first against the practice cap (50
  per rolling 30 days, all purposes together) and the country allow-list
  (`PORTAL_SMS_ALLOWED_COUNTRY_CODES`, default `1`). `new_browser` texts also
  have the per-client limit under *Rate limits*. A refusal is 409
  `PORTAL_TEXT_LIMIT`, never a silent skip, and sends the client to the
  practice for an in-session code.

The per-practice resolver (`register_portal_sms_policy(resolver)`) returns the
policy and its caps for one practice; with none registered, the settings apply
to every practice. The password design has the same resolver, ledger table,
cap, allow-list and 409, and specifies their mechanics in more detail. Its
policy values and ledger purposes belong to that design and aren't built.

## Shared devices and older ones

- **Shared family devices**: the portal asks before creating a passkey and
  doesn't create one on a shared device. A family tablet the client signs in
  on with link plus text still becomes a known browser for that client, so it
  can later approve a sign-in. The devices list shows it.
- **Older devices**: no passkey offer, and the fallback floor applies. Passkeys
  need iOS 16, Android 9 with current Play Services, macOS 13, or Windows 10
  with Hello.
- **Cross-device**: a client on a borrowed computer can use the phone's passkey
  through the browser's QR code flow. The borrowed computer is a new browser,
  so the second step applies, and approving from the phone is one tap.
- **Practice hosts**: a passkey is bound to the host it was created on. If a
  practice moves its portal, the old host can list the new one in
  `/.well-known/webauthn` (related origins) where browsers support it;
  otherwise clients sign in with the fallback and enroll again.

## Copy

| Where | Draft |
|---|---|
| Offer | **Sign in faster next time.** Use your face, fingerprint or phone passcode instead of waiting for a text. |
| Shared-device question | Is this device used only by you? · Yes, only me · Others use it too |
| Before saving | Your device will save a sign-in for {portal address} under {email}. Anyone who can open your saved passwords will see it. |
| Done | You're set. Next time, choose **Sign in with a passkey**. |
| New browser, waiting | Open the portal on a device you've used before to approve this sign-in. |
| New browser, other ways | Text me a code instead · Email me a sign-in link |
| Approval prompt | Approve this sign-in? {browser} · {time} · Approve · This wasn't me |
| New device email | A new device signed in to your {practice name} portal: {browser}, {time}. If this wasn't you, [this wasn't me]. |
| After *This wasn't me* | We signed that device out. {practice name} will help you keep your portal private. |
| Protected, new browser | Ask {practice name} to add this device. |
| Devices list heading | Your devices |
| Clinician card, protected | Only allow devices added in session |
| Clinician card, report | {client} said a sign-in on {date} wasn't them. |

## Data

All per-practice, in the practice schema, created by an OSS tenant migration
and included in the tenant template. No PK is named `id`. The RLS posture
matches `companion_sessions` and is proven by a provisioning test.

| Table | Columns |
|---|---|
| `portal_passkey_credentials` | `credential_id` PK, `patient_id`, `user_handle` (unique), `rp_id`, `public_key`, `sign_count`, `transports`, `aaguid`, `backup_eligible`, `backup_state`, `enrolled_via` (`sms`, `in_session`, `passkey`), `created_at`, `last_used_at`, `revoked_at`, `revoked_by` |
| `portal_passkey_challenges` | `challenge_hash` PK, `ceremony`, `patient_id` nullable, `expires_at` (300 s), `consumed_at` |
| `portal_known_browsers` | `browser_key` uuid PK, `patient_id`, `token_hash` (SHA-256), `browser_family`, `added_via` (`enrollment`, `approval`, `text`, `email`, `in_session`), `created_at`, `last_seen_at`, `revoked_at`, `revoked_by` |
| `portal_sign_in_approvals` | `approval_id` uuid PK, `patient_id`, `credential_id`, `browser_family`, `status` (`pending`, `approved`, `denied`, `expired`, `completed`), `completed_via`, `created_at`, `expires_at` (10 min) |
| `portal_client_protection` | `patient_id` PK, `protected` boolean, `set_by`, `set_at` |
| `portal_step_up_texts` | `text_id` uuid PK, `patient_id`, `purpose` (`enrollment`, `new_browser`, `fallback`), `sent_at`. No number, no code, no message |

Two new columns on existing tables:

- `companion_sessions.minted_via`, nullable text, values as in *How a session
  was minted*.
- `companion_auth_challenges.code_source` (`sms`, `in_session`), as in the
  password design.

### Audit events

- `PATIENT_PORTAL_PASSKEY_ENROLLED`, `PATIENT_PORTAL_PASSKEY_REMOVED`
- `PATIENT_PORTAL_PASSKEY_SIGNED_IN` (`browser`: `known` or `new`; `second_step`: `approval`, `text`, `email`)
- `PATIENT_PORTAL_SIGN_IN_APPROVAL_REQUESTED` / `_APPROVED` / `_DENIED`
- `PATIENT_PORTAL_NEW_DEVICE_REPORTED` (the *This wasn't me* link)
- `PATIENT_PORTAL_BROWSER_REMOVED` (actor: patient or clinician)
- `PATIENT_PORTAL_PROTECTED_MODE_CHANGED` (actor: clinician)
- `PATIENT_PORTAL_NEW_BROWSER_REFUSED` (protected mode)
- Shared with the password design: `SIGN_IN_CODE_GIVEN`, `TEXT_REFUSED`, and `SESSION_REDEEMED.second_factor`

Nothing logs a token, an assertion, an email address or a phone number.

### Rate limits

| What | Limit |
|---|---|
| Passkey authenticate begin/finish | per IP, plus 60/min per practice slug |
| Assertion failures per credential | 10/hour, then refuse until the hour passes |
| Pending sign-ins per client | 3 open at once, 10/hour |
| New-browser texts | inside the practice cap, and 3/day per client |
| *This wasn't me* | single use per link |
| Register begin/finish | 10/hour per session |

## What's reused from the clinician passkeys

Reused: `py_webauthn`; `passkey_ceremony.py` helpers; the hash-then-consume
challenge pattern; the sign-count clone check (moved to a shared helper); and,
on the frontend, `@simplewebauthn/browser` with the step timeouts and reset in
`passkeyCeremony.ts`. Not reused: Firebase tokens and `pablo_amr`, backup
codes, attestation trust roots, the hardware-key floor and the platform tables.
Clients aren't Firebase users; their principal is a portal session row in one
practice's schema. The RP ID comes from the portal host resolver rather than
`WEBAUTHN_RP_ID`.

A client of two practices has two patient rows, two sets of passkeys and two
sets of known browsers. Nothing joins them.

## Build slices

| Slice | Contents | Size |
|---|---|---|
| 1. Tables and stores | Five tables, the ledger, `minted_via` and `code_source`, stores, RLS; refresh carries `minted_via` forward | S |
| 2. Passkey ceremonies | Register (gated on `minted_via` and `chain_started_at`) and usernameless sign-in, UV, clone check, kill switch revokes, audit | M |
| 3. Known browsers and the second step | Cookie, approvals, new-browser text and email paths, notices, *This wasn't me*, devices list | M |
| 4. Protected mode | Setting, in-session-only registration, new browsers refused on the passkey path and on link plus text, keep-or-revoke on turning it on, optional device-bound check | S |
| 5. SMS policy and ledger | `every_sign_in` and `limited`, the three purposes, cap, country allow-list, 409, resolver (see *The SMS policy*) | S |
| 6. Clinician card | In-session code (shared), protected mode, devices, reports | S |
| 7. Portal screens | Offer, shared-device question, sign-in, waiting and approval, devices, copy | M |
| 8. Local-stack e2e | See *Slice 8 cases* | M |

### Slice 8 cases

The local stack runs with `PORTAL_SMS_POLICY=limited` and a virtual
authenticator. "No text" means the SMS fake recorded zero sends and
`portal_step_up_texts` gained zero rows, not that a field is missing.

1. Enroll: link plus text (one `enrollment` row), add a passkey.
2. Known-browser sign-in with the passkey: no text.
3. New browser approved from a known one: no text, the new browser becomes
   known, `new_device_signed_in` is sent.
4. New browser, text step: one `new_browser` row.
5. New browser, emailed-link step: no text, the sign-in completes in the
   pending browser.
6. *This wasn't me*: the browser's token and sessions are revoked.
7. Protected mode, passkey from a new browser: refused, no pending sign-in.
8. Protected mode, link plus text from a new browser: refused, no text sent.
9. Fallback floor, no platform authenticator: link plus text, one `fallback`
   row, no passkey offered.
10. Fallback floor, cap reached: 409 `PORTAL_TEXT_LIMIT`, then sign-in with
    an in-session code.

## Texts saved

Under `limited`, a client with a passkey receives the enrollment text, and
afterwards only a text when a new browser can't be approved from a known one
and the client chooses a text. A monthly portal user goes from about twelve
texts a year to about one. Clients on devices without passkey support stay at
today's rate, inside the cap.
