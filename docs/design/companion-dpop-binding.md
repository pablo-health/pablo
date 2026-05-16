# Companion device binding — DPoP and the install_id deviation

**Status:** active design — stage 1 implemented (THERAPY-xo0o), stage 2
(middleware enforcement) tracked under THERAPY-6qtr.

**Audience:** Pablo engineers, security reviewers, future-Kurt, anyone
reading the codebase six months from now wondering why the `DPoP`
middleware doesn't look like a textbook RFC 9449 implementation. This
doc is the reason.

**See also:**
- `docs/design/companion-thin-client.md` — the product-shape doc this
  security architecture sits underneath.
- THERAPY-xo0o (stage 1: enrollment + DPoP-ready schema), THERAPY-6qtr
  (stage 2: middleware proof enforcement).

---

## TL;DR

1. The Pablo Companion (Mac + Windows desktop app) is the only
   first-party native client. The web app + browser-based companion
   API consumers don't need device binding because they're already
   under the same-origin / sign-in protections of the Pablo frontend.
2. Companion auth uses **DPoP-style per-request signing**: a key in
   Secure Enclave (Mac) or TPM 2.0 / Microsoft Software KSP fallback
   (Windows) signs a proof JWT covering method + URL + nonce, included
   in a `DPoP` HTTP header.
3. **We deviate from RFC 9449 in one specific way:** the proof is
   bound to an `install_id` (a TOFU-registered device identity) rather
   than to the access token's `cnf.jkt` claim. Same security property,
   different binding key.
4. **Why we deviated:** Firebase mints our access tokens and we don't
   control their claims. Layering a Pablo-issued token over Firebase
   solely to add `cnf.jkt` would be weeks of work for no security gain.
5. **The schema is forward-compatible.** `companion_devices.jkt`
   already stores the RFC 7638 thumbprint; if we ever mint our own
   session tokens, the migration to `cnf.jkt`-bound RFC 9449 is a
   day's work, not a redesign.

---

## Background: RFC 9449 in one paragraph

DPoP (Demonstrating Proof of Possession, RFC 9449) is the OAuth 2.0
working group's answer to the "stolen bearer token is a skeleton key"
problem. The client generates a per-request short-lived JWT — the
*DPoP proof* — signed by a private key the client holds. The proof
carries claims `htm` (HTTP method), `htu` (URL), `iat` (timestamp),
and `jti` (unique nonce). The access token gets a `cnf.jkt` claim
(RFC 7800) containing the RFC 7638 thumbprint of the proof-signing
public key. The server-side middleware verifies (a) the proof's
signature, (b) the proof's claims match the request, (c) the proof
key's thumbprint matches the token's `cnf.jkt`.

The net effect: an attacker who steals the bearer token alone cannot
authenticate, because they cannot produce a proof signed by the
matching private key — and if the key is in Secure Enclave / TPM, the
attacker cannot extract it even with full filesystem access.

## Our deviation: install_id binding instead of `cnf.jkt`

Pablo's middleware verifies:

1. The Firebase id_token (HTTP `Authorization: Bearer …`) is valid
   and identifies a user. *Unchanged from current auth.*
2. The request carries an `X-Install-ID` header naming an enrolled
   companion device. The middleware looks up
   `companion_devices(user_id_from_token, install_id)` to retrieve
   the device's registered public key (JWK). If the row is missing,
   revoked, or belongs to a different user → **401**.
3. The request carries a `DPoP` header (compact JWS). The middleware
   verifies the signature against the stored public key, and checks
   the proof's `htm`/`htu`/`iat`/`jti` claims as RFC 9449 §4.3
   describes.

Compared to RFC 9449, the difference is purely in step 2: where the
RFC verifies "the proof key matches the access token's `cnf.jkt`," we
verify "the proof key matches the install_id's enrolled key, and the
install_id belongs to the authenticated user."

### Why we did this

| Reason | Cost of doing RFC-standard | Cost of our deviation |
|---|---|---|
| Firebase mints id_tokens | We'd have to mint our own session tokens that include `cnf.jkt` — weeks of work, ongoing maintenance of an identity layer over Firebase | None — Firebase token stays untouched |
| Multi-device per user | Each access token bound to one key; multi-device requires per-device tokens | One Firebase token works on every device the user enrolls |
| Per-device revocation | Revoke specific token via Firebase admin SDK, awkward UX | `DELETE FROM companion_devices WHERE install_id = ?` |
| Forward path | N/A — already standard | `jkt` column populated at enrollment; migration to `cnf.jkt` later is mechanical |

### Why we **could** do this safely

The security property RFC 9449 provides is "an attacker who has the
access token but not the private key cannot make valid requests." We
preserve that property as long as:

- We authenticate the user *separately* from the device proof. The
  Firebase id_token already does this — the middleware verifies it
  before looking at the proof.
- The `(install_id, user_id)` binding is checked on every request.
  An attacker can't take their own install_id and someone else's
  Firebase token; the lookup uses both as keys.
- The proof is bound to method + URL + nonce + freshness window.
  Same as RFC 9449. A captured proof works for at most one specific
  request within the clock-skew window, then jti cache rejects it.

These three checks together give the same end-state guarantee as
RFC 9449. We've moved where the binding lives, not what it does.

---

## Threat model

The table below walks every scenario we considered in the design
discussion. Two columns: how RFC 9449 (standard cnf.jkt binding)
handles it, how Pablo's install_id binding handles it. They should be
identical on every row — if any row diverges, the deviation has a
problem and we need to revisit.

| # | Threat | RFC 9449 (cnf.jkt) | Pablo (install_id) | Notes |
|---|---|---|---|---|
| 1 | Attacker captures the access token over the wire (logs, MITM, browser extension), nothing else | ❌ Blocked — needs proof signed by `cnf.jkt` key | ❌ Blocked — needs proof + install_id header + matching key | Same property, three required vs two |
| 2 | Attacker captures a single DPoP proof + the token (full request snapshot) | ⚠️ Replayable within iat-window unless server-nonce; jti cache blocks within TTL | ⚠️ Same | Both mitigated by jti cache + iat window |
| 3 | Malware reads Keychain (Mac) / Credential Manager (Windows) | ❌ Blocked — Secure Enclave / TPM key non-extractable. Software-KSP fallback (Windows w/o TPM): degraded — key is DPAPI-encrypted blob in user profile, malware running as user can read it | Same | `key_storage='software'` row exists in audit; can be alerted on |
| 4 | Attacker steals the access token AND knows a valid install_id (e.g. cross-user observation) | ❌ Blocked — install_id ≠ key; needs signed proof | ❌ Blocked — middleware checks install_id belongs to the user; cross-user pairings fail | Critical: install_id-user binding check is load-bearing |
| 5 | Attacker uses victim's install_id with their own Firebase token | N/A (no install_id concept) | ❌ Blocked — install_id must belong to authenticated user | Stage 2 middleware test: cross-user install_id → 401 |
| 6 | Attacker enrolls a fake companion (script-runs the enrollment endpoint with a freshly-minted key) | ⚠️ Possible — RFC 9449 doesn't address enrollment | ⚠️ Possible — App Attest (separate, lower-priority bead) closes this | Mitigation deferred; install_id revocation gives ops a knob |
| 7 | Attacker briefly bypasses Secure Enclave (e.g., 60s memory inspection), pre-computes future proofs | ⚠️ Mitigated by server-issued DPoP-Nonce (RFC 9449 §8) | ⚠️ Same gap | Server-nonce deferred in 6qtr; narrow threat |
| 8 | Stolen unlocked device | ❌ Not addressed by either scheme | ❌ Same | Idle-logout (THERAPY-a290) is the relevant control |
| 9 | OAuth authorization-code interception | ❌ Not addressed by either scheme | ❌ Same | PKCE is the answer; orthogonal to DPoP |
| 10 | Lost laptop, user reports it | ⚠️ DPoP can't help — the key is on the device | ⚠️ Same — install_id-bound key is on the device | DPoP is the wrong layer; see "Lost laptop is a different problem" below |
| 11 | TPM-less Windows machine | N/A | ⚠️ Graceful fallback: `key_storage='software'`. Same threat model as scenario 3's software case | Compliance team can query for hardware-bound coverage |
| 12 | Companion auth code intercepted between web and native app (loopback / custom scheme) | Out of scope (PKCE territory) | Same | The auth code itself is 60s TTL single-use, separate defense |
| 13 | User reinstalls OS / wipes companion | Existing flow: re-authenticate via OAuth | Same: new install_id, new key, new row; old row remains until ops/user revokes | Stale rows are visible in `GET /me/devices` (THERAPY-kcz0) for cleanup |
| 14 | Stolen Firebase refresh token | Firebase: refresh ATs without device proof → useless for protected endpoints | Same | Refresh path bypasses our middleware; ATs the refresh emits are still useless without install_id + proof |
| 15 | Stale device with revoked_at set tries to use a still-valid Firebase token | Token revoke required | Middleware rejects on `revoked_at IS NOT NULL` lookup | Faster + finer-grained than token revocation |

Rows 1–5 (the most common realistic attacks) are handled identically
between RFC 9449 and our scheme. Rows 6–9 are addressed by separate
controls in both. Rows 10–15 are areas where our scheme is equivalent
or better.

**No row shows our deviation as strictly weaker than RFC 9449.** That
is the security justification for shipping it.

### Lost laptop is a different problem

Threat #10 (lost / stolen unlocked laptop) deserves its own
discussion because the obvious read — "we revoke the install_id and
we're done" — undersells the gap between *device lost* and *device
revoked*, and because DPoP is **not the relevant control here at
all**. The hardware-bound key is on the laptop. An attacker holding
the laptop *is* the device from the proof's perspective; the key
signs proofs for them happily. RFC 9449 has the same gap for the
same reason.

**The thin-client architecture shrinks the realistic attack surface
to the browser session.** The companion in v1 has no PHI-browsing
UI: minimal main window, "Connected, mic ready, [Open Web
Dashboard]." A non-sophisticated attacker who picks up an unlocked
laptop can't browse patient records through the companion because
the companion doesn't show patient records. To touch PHI they need
either:

1. **The browser** — click `Start Session` on app.pablo.health, or
   read whatever logged-in tab is already open. The relevant
   control is web idle-logout (THERAPY-a290 — currently a launch
   blocker), **not** any companion-side defense.
2. **A script** that uses the companion's stored Firebase refresh
   token + device key to call backend APIs directly. Realistic for a
   sophisticated insider; rare for a "left laptop at a café"
   attacker.
3. **The OS-level Keychain / Credential Manager** — readable if the
   attacker has unlocked-user access. Full-disk encryption +
   require-Touch-ID-to-unlock-Keychain are OS-level controls Pablo
   recommends in the self-hosting / customer onboarding guide; they
   are not enforced by Pablo's code.

The controls that actually mitigate this scenario, in priority order:

| Control | Owned by | Status |
|---|---|---|
| Web idle-logout | Pablo (frontend) | THERAPY-a290, launch-blocker |
| Companion has no PHI-browsing UI | Pablo (design) | Already true in thin-client v1 |
| Self-serve `revoke device` button on the device list | Pablo (frontend + backend) | Not yet filed |
| Refresh-token TTL bounded to N hours | Pablo (Firebase config) | Audit task, not yet filed |
| Server-side session timeout regardless of token validity | Pablo (backend) | Not yet enforced |
| Full-disk encryption (FileVault / BitLocker) | OS-level / customer | Document in HIPAA self-hosting guide |
| Strong device login (Touch ID / Windows Hello / strong password) | OS-level / customer | Document in HIPAA self-hosting guide |
| Screen-lock idle timer | OS-level / customer | Document in HIPAA self-hosting guide |

DPoP does help the *cross-device* version of this story: revoking the
lost laptop's install_id doesn't affect the user's other enrolled
devices (one row update vs. a Firebase-wide token revoke). That's
real, but it's a usability win, not a defense against the realistic
threat itself.

**Where the doc was misleading on first pass:** the original row
implied DPoP gave us strictly-better lost-laptop protection. It
doesn't. DPoP and `cnf.jkt` are equally helpless when the key is
sitting on the same hardware the attacker is holding. The protection
lives at other layers — web idle-logout, refresh-token TTL,
OS-level disk encryption, self-serve revocation UX — and those are
where launch-readiness should be measured, not in the DPoP
architecture.

---

## Nonces — what we have, what we don't

Two nonce mechanisms exist in DPoP. Knowing which one we use matters:

### `jti` (client-generated, in every proof) — ✅ in stage 2

The companion picks a fresh random `jti` (UUID) for every DPoP proof.
The server caches seen `jti` values in an LRU (5-minute TTL, large
enough to comfortably exceed the iat clock-skew window). Replays
within the window are rejected.

Combined with the `iat ± 60s` window check, this blocks all
replay-from-capture attacks: an attacker who recorded a proof can't
re-use it more than once, and after 60 seconds the `iat` window
closes anyway.

### `DPoP-Nonce` (server-issued, RFC 9449 §8) — ❌ deferred

The server-issued nonce binds the proof to a specific server-issued
challenge, which the client must echo back in subsequent proofs. The
narrow extra guarantee: an attacker who briefly compromised the
device key (say 60s of Secure Enclave bypass) cannot pre-generate
future proofs, because they don't yet know the future nonce.

We deferred this in 6qtr because:

1. Brief-bypass-then-no-bypass is an exotic threat model. If the
   attacker has briefly bypassed Secure Enclave, they probably have
   ongoing access too.
2. It adds operational cost: the server has to issue, persist, and
   eventually expire nonces. Either a Redis structure or a long-lived
   LRU. Both add moving parts.
3. The companion code gets more complex: handle 401-with-DPoP-Nonce
   challenge, retry, include `nonce` claim. ~50 lines of additional
   client logic per platform.

If the threat model evolves (e.g., we get a CVE in our threat
landscape that this would address), file a follow-on bead and turn
it on. The 6qtr middleware should be structured so server-nonce can
be added without breaking changes.

---

## Stage rollout

### Stage 1: schema + enrollment (THERAPY-xo0o) — *shipped*

- `platform.companion_devices` table with DPoP-ready columns
- `/api/auth/native/exchange` accepts optional enrollment payload
- JWK structural validation + RFC 7638 thumbprint computation
- No middleware enforcement yet; companion submits keys, server
  records them, nothing else changes

Stage 1 is safe to ship before companions support signing because
existing clients without the enrollment payload continue to work
unchanged. The `enrollment` field on `ExchangeAuthCodeRequest` is
`Optional[CompanionEnrollment] = None`.

### Stage 2: proof enforcement middleware (THERAPY-6qtr) — *next*

- FastAPI middleware verifies `X-Install-ID` + `DPoP` on every
  authenticated request when `ENABLE_DPOP_VALIDATION=true`
- Per-request `last_seen` update via the touch path on
  `CompanionDeviceRepository`
- Behind feature flag while companions ship signing support

Critical correctness requirement: when the flag is on, **any
authenticated request from a companion-issued auth code must have a
DPoP proof** — there must be no opt-out by accident. See "Test
enforcement" below.

### Stage 3: companion-side signing (PABLO-D epic) — *separate repo*

- Mac: `SecKeyCreateRandomKey` + `kSecAttrTokenIDSecureEnclave`
- Windows: `CngKey.Create` w/ `MicrosoftPlatformCryptoProvider`
  (TPM 2.0); transparent fallback to
  `MicrosoftSoftwareKeyStorageProvider` (DPAPI-bound user-profile
  key) on TPM-less machines
- Per-request DPoP proof generation
- Lives in `pablo-health/pablo-companion`, tracked there

### Stage 4 (optional): App Attest / Play Integrity / Windows TPM AIK

Enrollment-time attestation that the requester is running our signed
binary on legitimate Apple/Microsoft hardware. Closes the "scripted
fake companion enrolling install_ids" gap (threat #6 above).

Cheap to add (~1 day per platform) but lower priority than getting
proof enforcement live.

---

## Test enforcement — "impossible to forget the header"

The risk: a future code change adds a new authenticated route and
forgets to apply the DPoP middleware. The route would silently accept
authenticated requests without a proof — the exact failure mode the
middleware is supposed to prevent.

**Defense-in-depth across three layers:**

### 1. Middleware-by-default, opt-out is explicit (stage 2)

The middleware applies to **all authenticated routes** by default.
The opt-out is a named decorator (`@dpop_exempt`) listed at the route
definition. Anything not exempted is enforced. Exemptions are:

- Pre-auth routes (`/api/auth/native/*`, `/api/health`, etc.) — no
  authenticated user yet, no install_id to check
- Inbound webhooks (Stripe, Plunk, etc.) — auth is by shared-secret
  signature, not user JWT
- Internal SaaS-only admin routes that don't accept companion clients

Every exempt route must justify itself in a code comment.

### 2. Route-enumeration test (stage 2)

A test that walks `app.routes` via FastAPI's introspection and
asserts every route is either:

- in the exempt list, **or**
- covered by a dependency / middleware that requires DPoP

Adding a new authenticated route without addressing one of those
two paths causes the test to fail. This is the **load-bearing test**
that makes the system fail-closed.

A bead for this test is filed alongside 6qtr; the test ships in the
same PR as the middleware.

### 3. Integration test that exercises the actual middleware stack

For at least one authenticated route per top-level prefix (`/api/
patients`, `/api/sessions`, `/api/me`, etc.), an end-to-end test
asserts:

- request with valid JWT + valid DPoP + valid install_id → 200
- request with valid JWT + no install_id → 401
- request with valid JWT + valid install_id + bad proof signature
  → 401
- request with valid JWT + valid install_id + proof for a different
  URL → 401
- request with valid JWT + revoked install_id → 401

These tests live in `backend/tests/test_dpop_middleware.py`.

### Stage-1 doesn't need this enforcement

Stage 1 only adds an optional field to an existing endpoint. The
backward-compat tests in `test_auth_routes.py` already cover the
"existing clients keep working" case. The enforcement risk doesn't
appear until stage 2.

---

## Backward compatibility

The stage-1 changes were designed so every existing API caller keeps
working unmodified:

- `ExchangeAuthCodeRequest.enrollment` is `Optional` with default
  `None`. Pydantic v2 ignores unknown fields by default, so legacy
  clients sending the old payload work; new clients sending the new
  payload work; clients sending malformed enrollment data fail
  validation cleanly with 422 (no exchange of the auth code).
- `PendingAuthCode.firebase_uid` is `Optional` with default `None`.
  Redis-stored auth codes from the deploy window (≤60s TTL) without
  the new field deserialize cleanly with `firebase_uid=None`. The
  enrollment path handles `firebase_uid is None` by skipping
  enrollment and logging — token exchange still succeeds.
- Enrollment failures (bad JWK, DB error, missing identity row) do
  **not** block token exchange. The companion can retry enrollment
  on next launch when it notices DPoP-protected endpoints rejecting
  it.
- Frontend Next.js proxy at `frontend/app/api/auth/native/exchange/
  route.ts` is a transparent body-passthrough. No frontend change is
  required.
- Existing 1029 unit tests pass unchanged.

The OSS / SaaS boundary is preserved: every change is in OSS pablo;
the SaaS overlay imports nothing new.

---

## Operational primitives

Even with weakened-by-design `key_storage='software'` rows, the
operational story is robust:

- **Per-device revocation**: ops updates
  `companion_devices.revoked_at`; middleware rejects within the next
  request. Other devices for the same user keep working.
- **Device list UX (THERAPY-kcz0)**: `GET /me/devices` returns the
  user's enrolled devices with a short `jkt_fingerprint` so users
  can recognize "Mac Mini · a3f9c2e1" and self-serve revoke.
- **Audit posture**: rows record `enrolled_at`, `last_seen`,
  `key_storage`, `platform`, `os_version`. Compliance team can answer
  "what fraction of active devices are hardware-bound" with a single
  SQL query.
- **Anomaly signal**: same `install_id` appearing from a
  geographically novel IP is a future-detection signal. Not built
  yet; the data is there for it.

---

## What this doc deliberately doesn't cover

- The web frontend's auth (separate concern; same-origin sign-in).
- Inbound webhook signature verification — see CLAUDE.md guardrail
  S4 and `backend/saas/webhooks/plunk.py`.
- HIPAA audit logs for device events — currently a Python `logger.
  info` line; upgrade to a structured audit-action enum is a follow-
  on if compliance asks.
- Cross-device session handoff. Companion thin-client design
  explicitly excludes this.

---

## Glossary

- **DPoP** — Demonstrating Proof of Possession, RFC 9449. The HTTP
  header carrying the proof JWT.
- **`cnf.jkt`** — RFC 7800 "confirmation" claim on an access token,
  containing the RFC 7638 thumbprint of the proof-signing public key.
  The standard binding key. **We do not use this.**
- **JWK** — JSON Web Key, RFC 7517. JSON-serialized public key.
- **`jkt`** — JWK thumbprint, RFC 7638. SHA-256 of the canonical-JSON
  of the JWK's required members, base64url-encoded.
- **TOFU** — Trust on First Use. The pattern of "we trust the public
  key the first time we see it, then remember it." How our enrollment
  works.
- **Secure Enclave** — Apple's hardware-isolated key storage. Keys
  generated with `kSecAttrTokenIDSecureEnclave` are non-extractable.
- **TPM** — Trusted Platform Module. Hardware key storage on PCs.
  Windows uses TPM 2.0 via `Microsoft Platform Crypto Provider`.
- **Microsoft Software KSP** — `Microsoft Software Key Storage
  Provider`, the software-backed fallback when no TPM is present.
  Stores keys DPAPI-encrypted in the user profile.
- **install_id** — Random UUID generated client-side at first launch
  and persisted alongside the device key. Our binding identifier.
- **App Attest** — Apple's `DCAppAttestService`. Hardware-attested
  proof that an app is the official one running on legitimate Apple
  hardware. Stage-4 follow-on.
