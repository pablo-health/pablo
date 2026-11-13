# Portal sign-in without a text at every visit: the link plus a password

Status: proposed. Modules touched: `backend/app/portal/` (new `passwords.py`,
`password_blocklist.py`, `sms_policy.py`, `in_session_code.py`; changes to
`routes.py`, `recovery.py`, `service.py`, `store.py`, `db_store.py`), the
portal frontend, and the clinician's portal-access card on the chart. New
dependency: `argon2-cffi`.

## The problem

Portal sign-in takes two factors on two channels. The **magic link** arrives
by email and proves the client holds the inbox. The **code** arrives by text
and proves the client holds the phone on the chart (`delivery.py`, `otp.py`).
Redemption needs both (`POST /api/patient/auth/redeem`), and a session chain
lasts at most 30 days (`session_max_lifetime_seconds`). After that, or on a new
browser, the client signs in again and gets another text.

The text code was chosen over a date of birth on purpose (`otp.py`). A date of
birth has little entropy, whoever received a forwarded link often knows it, and
it can't be changed once someone guesses it. An emailed code is no better. It
lands in the same inbox as the link, so the two together prove one thing once.

So every sign-in costs one text. **A deployment may run the portal with an SMS
channel it wants to use rarely**, and today it can't. This design adds a second
factor that costs nothing per use, a password the client chooses, and a policy
that limits the SMS channel to enrollment.

Today's flow stays the default. Link plus text each time is the simplest thing
for a client: there is nothing to remember and nothing to reset. The password
factor is opt-in, per deployment or per practice.

## Decision summary

1. **Sign-in is the emailed link plus a password the client chose.** That's
   two factors: something they hold (the inbox) and something they know. **A
   password never signs in alone.** No route accepts a password without a valid
   link.
2. **Setting the password needs proof of identity.** That proof is the one
   text code (the existing redemption) or, when no text can be used, an
   **in-session code** the clinician reads aloud. The password is set in the
   same request that redeems, so no session exists before it does.
3. **Reset goes only through the clinician.** The chart card issues a
   temporary password that works once, expires within an hour, and is shown to
   the clinician once. The client must set their own password right after
   using it. There is no self-service reset by email (see *Why reset is the
   clinician's*).
4. **Argon2id**, via `argon2-cffi`, with m=46 MiB, t=2, p=1 (above the OWASP
   minimum on both memory and passes). The parameters are stored in the
   encoded hash string, the hash is redone at sign-in when they're raised, and
   a deployment may add an optional, versioned HMAC pepper held outside the
   database.
5. **NIST SP 800-63B rules**: at least 8 characters (12 recommended,
   configurable), up to 128, every character allowed, no composition rules, no
   forced rotation, and an offline blocklist of common and breached passwords
   shipped with the engine.
6. **`PORTAL_SMS_POLICY`**: `every_sign_in` (default, today's flow, no
   password) or `enrollment_only` (one text at first sign-in, then link plus
   password). Every step-up text goes in a ledger and is checked against a
   per-practice cap and a country allow-list. A per-practice resolver lets the
   policy differ by practice.

## Threat model

| Attacker holds | Link plus text (today) | Link plus password |
|---|---|---|
| A forwarded or intercepted link | Can't redeem: no phone | Can't redeem: no password. Can't enroll: the first sign-in needs the text or the in-session code |
| The client's inbox, long-term (e.g. a family member) | Can request links, can't get codes | Can request links. Needs the password too, and the throttle bounds guessing |
| The client's number via SIM swap | Needs the inbox too | Not used after enrollment |
| A password the client reused elsewhere and leaked | n/a | Needs the inbox too. This is the main weakness against a phone, and the blocklist only catches known leaks |
| A phishing page imitating the portal | Can relay link and code in real time | Can relay link and password. Neither flow is phishing-resistant; passkeys would be (see *Later: passkeys*) |
| The database, or a backup of it | Code hashes for codes already spent | Argon2id hashes, uncrackable at scale for strong passwords, and useless without the pepper when one is set |
| A stranger trying to lock a client out | n/a | Can't: a password attempt needs a valid link first |

The crux, again, is that **nothing reachable with the link alone may set or
change a password.** Every way a password is set is paired with a second proof:
the text code, the in-session code, or a temporary password the clinician
issued.

## Flows

### First sign-in: the one text, and a password

1. The clinician sends the invitation. Nothing changes.
2. The client opens the link and asks for a code (`request-code`, unchanged
   apart from the SMS policy check below).
3. The link page shows the code field and a **choose a password** field
   together. `POST /api/patient/auth/redeem` takes `new_password` alongside
   `otp`, and requires it when the policy is `enrollment_only` and the client
   has no password. The code is verified, the password is checked against the
   rules and hashed, and the session is minted, all in one transaction. If any
   step fails, nothing is stored.

`set_via` records `sms`. Under `every_sign_in`, `new_password` is ignored and
the flow is exactly today's.

### When the text can't be used: the in-session code

Some clients have no mobile number on the chart, have only a landline, or have
a phone that can't receive texts from a short code. Others belong to a
practice that has used its step-up cap. For them, **the clinician confirms
identity in person or on a call they are already having**:

1. On the chart's portal-access card, the clinician chooses *Give a sign-in
   code*. The server uses the client's outstanding invitation, or issues one
   (which emails the link as usual). It generates a six-digit code, stores its
   peppered hash on that invitation's challenge row with a 10-minute window and
   `code_source = 'in_session'`, and shows the code to the clinician once.
2. The clinician reads it to the client, who types it where the text code
   would go, chooses a password, and signs in. Same route, same 5-attempt cap,
   same uniform 401.

The link proves the inbox, and the clinician proves the person. `set_via`
records `in_session`. Issuing a code is audited with the clinician as the
actor.

### Every later sign-in: the link plus the password

1. On the practice's portal page, the client asks for a sign-in link. This is
   the existing `POST /api/portal/practices/{slug}/recover`, unchanged:
   uniform 202, CAPTCHA, per-address and per-practice windows, active grant
   only.
2. The link page shows a password field.
   `POST /api/patient/auth/redeem-password` takes `{token, password}` and runs
   in the same order as redeem: signature, the per-invitation window, the
   practice schema, the throttle, then the hash check. On success it consumes
   the invitation and mints a session through `PortalAuthService`, so the
   lifetime, the 30-day chain ceiling and revocation are unchanged.

No text is sent. When the chain ceiling arrives, the client signs in this way
again.

### Why reset is the clinician's

A self-service reset would email a reset link to the inbox, and the reset link
would let its holder choose a new password. Whoever holds the inbox could then
set a password and sign in with the inbox again. The two factors would collapse
into one, the inbox, which is exactly the forwarded-link attacker this design
exists to stop. A reset by text would be sound, but it brings the per-use cost
back for the one event clients repeat most. So the reset goes through the
person who can recognize the client:

1. The clinician chooses *Reset sign-in* on the portal-access card.
2. The server generates a **temporary password** of 12 characters from an
   alphabet without lookalike characters, hashes it with the same Argon2id
   parameters, sets `must_change = true` and `temp_expires_at = now + 60 min`,
   clears the throttle, revokes the client's live sessions, and shows the
   temporary password to the clinician **once**.
3. The clinician gives it to the client in session or on a call.
4. The client asks for a sign-in link and signs in with the temporary password.
   The session it mints can reach only `POST /api/patient/auth/set-password`,
   and every other patient route answers 403 until the client chooses a
   password. Then `must_change` clears and the session continues.

The temporary password works for one sign-in. After 60 minutes, or once used,
it can't be used again, and the client needs another reset. The reset, its
use, and the new password are each audited. `set_via` records `clinician_reset`.

Turning portal access off (the existing kill switch) also deletes the client's
password row. If access is turned back on, the client enrolls again with a text
or an in-session code.

## Hashing

**Argon2id, never encryption.** A password is stored only as an Argon2id hash;
nothing can turn it back into the password, and nothing in the engine tries.

| Parameter | Value | Note |
|---|---|---|
| Algorithm | Argon2id, version 19 | RFC 9106; `argon2-cffi` `PasswordHasher(type=Type.ID)` |
| Memory | 46 MiB (`m=47104`) | OWASP's first option is 46 MiB with t=1; its floor is 19 MiB with t=2 |
| Passes | t=2 | Above both OWASP options at this memory size |
| Parallelism | p=1 | One thread per hash, so concurrent sign-ins don't fan out across cores |
| Salt | 16 random bytes per hash | `argon2-cffi` default |
| Output | 32 bytes, encoded | `$argon2id$v=19$m=47104,t=2,p=1$<salt>$<hash>` |

**Measured**: median 57 ms (max 61 ms) per verify on an Apple M1 Max, versus
20 ms at the OWASP floor (m=19 MiB, t=2). A cloud vCPU is typically slower than
that, so expect about 80–150 ms per verify. **A build-time check runs the
hasher on the deployment's instance class and fails outside 50–500 ms**, so
parameters that are too weak or too slow are caught before release rather than
discovered in production.

**Memory under load.** Each verify holds 46 MiB while it runs. Hashing runs
behind a process-wide semaphore (default 4 concurrent hashes, so 184 MiB at
most), and the per-IP and per-practice limits are checked before a hash is
attempted. A burst of sign-ins therefore queues rather than exhausting the
instance's memory.

**The parameters travel with the hash.** Verification reads them from the
encoded string, so raising them never breaks existing hashes. On every
successful sign-in, `PasswordHasher.check_needs_rehash` compares the stored
parameters to the current ones, and if they're weaker, the password the client
just typed is hashed again and stored in the same transaction.

**Constant time and no account oracle.** `argon2-cffi` compares in constant
time. Every refusal that gets past the invite signature runs exactly one
Argon2id verify. When there is no stored hash to check (an unknown or consumed
invitation, or a client with no password), the verify runs against a dummy hash
computed once at startup with the current parameters. Timing therefore doesn't
reveal which links are live or which clients have set a password.

### The pepper (optional)

A deployment may set `PORTAL_PASSWORD_PEPPER` (from its secret store, never
from the database). The value hashed is then
`HMAC-SHA256(pepper, password)` rather than the password itself. Pre-hashing to
a fixed 32 bytes also removes any question of password length inside Argon2.

What it buys: a stolen database or backup yields hashes that can't be attacked
at all without the pepper, which lives somewhere else. That matters more here
than it may seem, because the harm of a cracked client password is mostly to
the client's *other* accounts where they reused it, starting with the very
inbox that holds the link.

What it costs: rotation. An HMAC pepper can't be rotated without the password,
so each row records `pepper_version`. A new version applies to new hashes and,
through the rehash step above, to each client at their next sign-in, while old
versions stay readable until no row uses them. Losing a pepper is worse than
leaking it: every client hashed under it needs a clinician reset. Keep versions
retained in the secret store, and rotate only on suspected compromise, not on a
schedule.

The engine treats the pepper as optional, and with no pepper configured it
hashes the password directly. A self-hosted deployment that can't hold a second
secret safely is better off without one than with one it might lose.

## Password rules

Per NIST SP 800-63B §3.1.1 (2024 revision):

- **Length**: at least `PORTAL_PASSWORD_MIN_LENGTH` (default 12, never below
  8), at most 128 characters, counted as Unicode code points after NFKC
  normalization.
- **Every character allowed**, including spaces and emoji. No composition
  rules (no "one digit, one symbol"), and no hints or knowledge questions.
- **No forced rotation.** A change is required only after a clinician reset,
  or when the blocklist later flags a password at sign-in (the client is asked
  to choose a new one; the sign-in itself still succeeds).
- **Blocklist, offline.** The engine ships a list of the 100,000 most common
  passwords from public breach corpora
  (`backend/app/portal/data/common_passwords.txt.gz`, about 400 KB, MIT-licensed
  source recorded in the file header). It also checks context words: the
  practice's name and the local part of the client's email. There are no
  external calls, so a self-hosted deployment needs nothing outside its own
  network.
- **Show while typing**: the password field has a show/hide toggle, and the
  page allows paste and password managers (`autocomplete="new-password"` /
  `"current-password"`).

A refused password gets one plain reason (too short, or too common), never a
strength meter.

## Throttling

| Limit | Window | Effect |
|---|---|---|
| Failed passwords per client | 10 per rolling hour | Then `locked_until = now + 15 min`; doubles per repeat, capped at 4 hours. Never permanent, and it clears on success or a clinician reset |
| Per invitation (existing) | 10 redeems per hour | Unchanged |
| Per IP | `portal_redeem_ip_rate_per_min` | Unchanged route dependency |
| Per practice | 120 password attempts per minute per slug | Bounds spraying across a practice's clients |
| Sign-in link requests (existing) | per address and per practice | Unchanged |

A stranger can't trigger a lockout, because a password attempt needs a valid
invite token first. Only someone holding the client's link can spend the
client's attempts.

**Every failure is the uniform 401**: wrong password, locked, consumed or
expired link, no password set. A locked client sees the same message as a
wrong password. The link page offers *Ask {practice name} to reset your
sign-in* after the third failure, which is a client-side count that reveals
nothing new.

## The SMS policy

`PORTAL_SMS_POLICY`:

- `every_sign_in` (default): today's flow. A text at every link redemption,
  no password, no counting.
- `enrollment_only`: a text only for a client who has no password (first sign-in,
  or after access was withdrawn and granted again). Every step-up text is
  written to `portal_step_up_texts` and checked against the caps first.

There is no recovery text under either policy, because a reset is the
clinician's.

A deployment may register a per-practice resolver
(`register_portal_sms_policy(resolver)`, the same registration pattern as the
portal section gate). The resolver returns the policy and its caps for one
practice. With none registered, the settings apply to every practice.

| Cap (under `enrollment_only`) | Default |
|---|---|
| Step-up texts per practice per rolling 30 days | 50 |
| Countries | `PORTAL_SMS_ALLOWED_COUNTRY_CODES`, default `1` |

**A cap is never a silent skip.** When one is reached, `request-code` returns
409 `PORTAL_TEXT_LIMIT` and the link page sends the client to the practice for
an in-session code. The 409 comes only after the invite signature verifies, so
it tells a stranger nothing. The refusal is audited, and the clinician's card
shows it.

## Copy

Drafts per `docs/reference/copy-style.md`: second person, no machinery, no
gendered pronouns. The reasoning stays in this doc.

| Where | Draft |
|---|---|
| First sign-in, password field label | Choose a password |
| First sign-in, hint | At least 12 characters. A short phrase works well. |
| Too short | Use at least 12 characters. |
| Too common | That password is too easy to guess. Try a phrase only you would know. |
| Show/hide | Show · Hide |
| Later sign-in, field label | Your password |
| Wrong password (uniform) | That didn't work. Check your password and try again. |
| After three failures | Forgot your password? Ask {practice name} to reset your sign-in. |
| Portal page | Email me a sign-in link |
| Link sent | Check your email for a link from {practice name}. |
| Temporary password used | Choose a new password to finish signing in. |
| Text limit reached (409) | We can't send a text right now. Ask {practice name} for a sign-in code. |
| Entering an in-session code | Enter the code {practice name} gave you. |
| Clinician card, reset | Reset sign-in |
| Clinician reset display | Give this to your client. It works once, for the next hour: **{temporary password}** |
| Clinician card, sign-in code | Give a sign-in code |
| Clinician code display | Read this code to your client. It works for 10 minutes: **{code}** |
| Clinician card, refused text | Couldn't send {client}'s sign-in text on {date}. Give them a sign-in code. |

## Data

All new state is **per-practice**, in the practice schema like
`companion_sessions` and `companion_auth_challenges`. It's created by an OSS
tenant migration and included in the tenant template. No primary key is named
`id`. The RLS posture matches the two companion tables, and the build slice
proves it against `enable_rls_on_schema` with a provisioning test before the
migration lands.

**`portal_client_passwords`**

| Column | Type | Note |
|---|---|---|
| `patient_id` | uuid PK | one password per client per practice |
| `password_hash` | text | encoded Argon2id string |
| `pepper_version` | smallint, nullable | `NULL` when unpeppered |
| `set_via` | text | `sms`, `in_session`, `clinician_reset` |
| `must_change` | boolean | true for a clinician's temporary password |
| `temp_expires_at` | timestamptz, nullable | set only with `must_change` |
| `failed_count` | smallint | in the current window |
| `failed_window_started_at` | timestamptz, nullable | |
| `locked_until` | timestamptz, nullable | |
| `created_at`, `updated_at` | timestamptz | |

**`portal_step_up_texts`**: the ledger the caps read. `text_id` uuid PK,
`patient_id`, `purpose` (`enrollment`, `sign_in`), `sent_at`. No number, no
code, no message.

**`companion_auth_challenges`**: one new nullable column, `code_source`
(`sms` / `in_session`).

### Audit events

New `AuditAction` values, written through `AuditService` with the patient as
the resource. None of them ever carries the password, the hash or a code:

- `PATIENT_PORTAL_PASSWORD_SET` (actor: patient; `set_via`)
- `PATIENT_PORTAL_PASSWORD_SIGN_IN` (actor: patient).
  `PATIENT_PORTAL_SESSION_REDEEMED` stays for redemptions and gains a
  `second_factor` detail (`sms`, `in_session`, `password`)
- `PATIENT_PORTAL_PASSWORD_SIGN_IN_FAILED` (actor: patient; `reason`:
  `wrong`, `locked`, `temp_expired`), recorded for the client the link names.
  Failures before the signature verifies go only to the rate-limit logs
- `PATIENT_PORTAL_PASSWORD_RESET` (actor: clinician)
- `PATIENT_PORTAL_SIGN_IN_CODE_GIVEN` (actor: clinician)
- `PATIENT_PORTAL_TEXT_REFUSED` (`cap`: `practice` or `country`)

## Passwords against passkeys

| | Link plus password | Passkey (later) |
|---|---|---|
| Phishing | Relayable in real time, like link plus text | Resistant: bound to the portal's origin |
| A partner on the same Apple or Google account | Doesn't see the password unless it's saved to that shared manager | A synced passkey appears on every device signed into the account, which is a real risk where the partner is the person the client is protecting themselves from |
| Client familiarity | Everyone knows passwords | Many clients haven't used a passkey; prompts differ by device |
| Older phones and shared computers | Work everywhere | Need recent OS versions; shared devices are unsafe |
| Forgotten or lost | Clinician reset | Synced passkeys usually return on a new phone, otherwise clinician |
| Build size | About 2 weeks across six slices | About 3 weeks, plus per-host RP IDs |

Passwords lead because every client can use one on any device today, they
don't follow the client onto a shared cloud account, and they cost the least to
build. Passkeys are the stronger factor, and they come later as an option for
the clients who want one.

## Later: passkeys

A client who has signed in with the link plus a password may add a passkey,
created with user verification required, which is then accepted in place of
the password. A passkey with user verification is a multi-factor cryptographic
authenticator (WebAuthn L2 §6.2.3; NIST SP 800-63B §5.1.9), so it may also
sign in without the link.

The shape, drafted earlier and kept for when this is picked up:

- Credentials are per practice and per client, in the practice schema, with a
  random user handle (never the patient id).
- The RP ID is the portal host the passkey was created on, so moving a practice
  to a new host needs related origins (`/.well-known/webauthn`) or
  re-enrollment.
- Reuse `py_webauthn`, `passkey_ceremony.py` helpers and the frontend
  `passkeyCeremony.ts` step timeouts from the clinician passkeys. The Firebase
  token, backup codes and platform tables are not reused, because clients
  aren't Firebase users.
- The kill switch revokes passkeys, and enrollment asks whether the device is
  shared before creating one.

## Build slices

| Slice | Contents | Size |
|---|---|---|
| 1. Tables and stores | Tenant migration, template, ORM rows, stores, RLS registration, `code_source` column | S (1–2 days) |
| 2. Hashing and rules | `argon2-cffi` dependency, hasher with parameters, semaphore, dummy hash, rehash, optional pepper, blocklist, the build-time timing check | S (2 days) |
| 3. Password sign-in | `new_password` on redeem, `redeem-password`, `set-password` with the `must_change` gate, throttling, audit, kill switch | M (3 days) |
| 4. SMS policy and ledger | `PORTAL_SMS_POLICY`, resolver, ledger, cap, country allow-list, 409 | S (2 days) |
| 5. Clinician card | *Give a sign-in code* and *Reset sign-in*, both shown once, audited | S (2 days) |
| 6. Portal screens and e2e | Password fields, show/hide, messages from the copy table; local-stack e2e covering the first sign-in, a later sign-in with no text, the cap, a reset and the kill switch | M (3–4 days) |

`enrollment_only` shouldn't be offered until slices 1–5 have landed.

## Texts saved

Under `enrollment_only`, a client receives one text when they first sign in,
and none after that unless their access is withdrawn and granted again. A
client who uses the portal about monthly receives about twelve texts a year
under `every_sign_in`, and one in total under `enrollment_only`.
