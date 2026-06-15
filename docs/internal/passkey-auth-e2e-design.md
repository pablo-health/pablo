# Passkey Auth — Test Design (API integration + browser e2e)

> Companion to `docs/internal/passkey-auth-build-spec.md`. Covers **how we
> prove the passkey factor works**, at two layers: backend API integration
> tests (PABLO-egm.6) and a full browser e2e (PABLO-egm.7). The headline:
> passkeys are *far* simpler to test end-to-end than the TOTP second factor
> they replace, because WebAuthn has a first-class **virtual authenticator**
> built into Chrome DevTools Protocol — no hardware, no biometric prompt, no
> shared TOTP secret, no clock.

---

## Why this is simpler than the TOTP flow

The thing that made auth e2e painful before was the second factor. TOTP needs a
shared secret provisioned out of band, a code computed at test time (`pyotp` +
the seed in a secret store), and it's clock-sensitive and flaky. That machinery
is also why the only authenticated e2e historically ran against a *deployed*
environment with a pinned, pre-provisioned user — you couldn't stand the second
factor up locally.

WebAuthn removes all of that:

- **CDP virtual authenticator.** `WebAuthn.addVirtualAuthenticator` creates a
  software authenticator inside the test browser that responds to ceremonies
  automatically. You set `hasResidentKey`, `hasUserVerification`,
  `isUserVerified: true`, and registration/assertion just work — no UI to click,
  no fingerprint, no QR code. Playwright drives this over a `CDPSession`.
- **Real crypto, not mocks.** The virtual authenticator produces genuine WebAuthn
  assertions, so the backend's real `py_webauthn` verify path is exercised
  end-to-end. We are not stubbing the security-critical code.
- **No secrets, no clock.** Nothing to provision, nothing time-sensitive.
- **Deterministic failure injection.** CDP lets you flip `isUserVerified` off,
  read/set the sign count, and add/remove credentials — so the *negative* tests
  (UV-required rejection, clone detection, revocation) are scriptable, not
  best-effort.

The practical payoff: **authenticated e2e can finally run locally / in CI**
against an ephemeral user, instead of only against a deployed pinned account.

### The catch (what's genuinely harder)

WebAuthn buys determinism by being strict about identity, and that strictness is
the friction:

- **Origin-bound.** The authenticator and the backend must agree on
  `expected_origin` and `expected_rp_id`. The RP id must be a registrable suffix
  of the origin. You cannot point the e2e at an arbitrary host — the test
  backend's `webauthn_origins` / `webauthn_rp_id` must include the origin the
  test browser actually serves from (e.g. a test-only `http://localhost` entry,
  or the deployed origin when running against a real environment). This is the
  single most common source of "works in the build spec, fails in the test."
- **The custom-token mint needs Firebase.** `authenticate/finish` ends in
  `create_custom_token(...)` → the browser calls `signInWithCustomToken()`. A
  fully local browser e2e of that last hop needs the **Firebase emulator** (or a
  dev project's credentials). The assertion-verify + claim-stamp logic *before*
  the mint is fully testable at the API layer without Firebase — so put the
  crypto coverage there (see API layer below) and reserve the browser e2e for
  the round-trip, running it where a Firebase token can actually be minted.
- **Chromium-only.** The CDP virtual authenticator is Chromium-only. Safari /
  iOS and Firefox are real passkey platforms but are out of scope for the
  virtual authenticator — cover them with manual or device-lab passes, not this
  suite.
- **Two paths the browser can't reach.** The native/desktop code-exchange path
  (`routes/auth.py`) is not a browser flow, and cross-device hybrid
  (QR + Bluetooth) cannot be virtualized at all. The first gets an API
  integration test; the second is manual/real-device only. Call both out so
  "e2e is green" is never read as "every path is covered."

---

## Layer 1 — API integration tests (PABLO-egm.6)

**Where:** the backend test suite (`backend/tests/` / the integration tests that
run against a real Postgres), not unit tests with a mocked DB — the challenge
single-use/consume and credential lookup are DB behaviors.

**How to produce assertions without a browser:** `py_webauthn` (and equivalent
software-authenticator helpers) can *build* a registration/authentication
response as well as verify one. Generate a keypair in the test, answer the
server's `begin` options with a real signed response, and post it to `finish`.
This keeps the crypto real while staying in pytest.

**The matrix to prove** (this is the whole point — the enforcement seam, not the
happy path):

| Case | Expected |
|---|---|
| register/begin → finish with a valid response | 201, a `passkey_credentials` row, challenge consumed |
| authenticate/begin → finish with a valid assertion | 200, returns a custom token; `pablo_amr` contains `"webauthn"` |
| a `pablo_amr` session hits a gated route | `require_mfa` passes (the factor is honored) |
| a **first-factor-only** Firebase token (no `pablo_amr`, no `sign_in_second_factor`) | `require_mfa` rejects → 403 `MFA_REQUIRED` |
| replay a consumed challenge | 400 |
| expired / unknown challenge | 400 |
| `new_sign_count <= stored` (clone), excluding legit 0/0 | reject + audited |
| assertion against a `revoked_at IS NOT NULL` credential | 401 |
| wrong origin / wrong RP id | verify fails |
| UV not performed when `user_verification="required"` | verify fails |
| **the native-code path** (`routes/auth.py`) honors `pablo_amr` too | not first-factor-only after cutover |

The last row is load-bearing: the build spec's H3 / Finding F6 is that
`routes/auth.py` is a *second, parallel* enforcement point. The integration
suite must assert **both** `providers.py:123` and the native gate accept the
passkey factor, or the desktop client silently diverges.

**Negative-claim guard:** add a test that a token carrying `pablo_amr` but with
no server-verified assertion behind it cannot be produced — i.e. the claim is
only ever minted in `authenticate/finish` after a real verify. This is the
test that keeps H1 honest.

---

## Layer 2 — Browser e2e (PABLO-egm.7)

**Where:** the OSS Playwright suite (`frontend/e2e/`, `frontend/playwright.config.ts`).
Today that directory holds a single unauthenticated scaffold
(`patients.spec.ts`) and is not yet a CI gate — this work establishes the first
*authenticated* e2e there.

**Setup (per spec):**
```ts
const client = await page.context().newCDPSession(page)
await client.send('WebAuthn.enable')
const { authenticatorId } = await client.send('WebAuthn.addVirtualAuthenticator', {
  options: {
    protocol: 'ctap2',
    transport: 'internal',          // platform authenticator (Touch ID-style)
    hasResidentKey: true,
    hasUserVerification: true,
    isUserVerified: true,           // flip to false for the UV-required negative test
  },
})
```

**Canonical happy-path spec** (mirror the two-layer pattern used by the
storage-upload e2e — app round-trip *plus* direct backend inspection):
1. Sign in (first factor) and reach passkey enrollment in `lib/auth/`.
2. Enroll a passkey — `@simplewebauthn/browser` `startRegistration` is answered
   by the virtual authenticator; assert a `platform.passkey_credentials` row
   exists (layer-2 inspection).
3. Sign out.
4. Sign in with the passkey — `startAuthentication` → `authenticate/finish` →
   `signInWithCustomToken`.
5. Navigate to a gated (PHI) page and assert it loads — i.e. the minted session's
   `pablo_amr` actually satisfies the gate through the real middleware.

**Negative spec:** create the authenticator with `isUserVerified: false` and
assert enrollment/login is rejected (UV-required is what makes the passkey a
*second factor*, not mere possession — build-spec H6).

**Origin/RP config:** the environment the e2e runs against must list its origin
in `webauthn_origins` and set `webauthn_rp_id` to a registrable suffix of it.
Document the test origin explicitly in the spec so a failure reads as
"origin not allowed," not "WebAuthn broken."

---

## What good looks like

- The crypto + enforcement matrix lives in **Layer 1** (fast, deterministic, no
  Firebase, no browser) — that's where correctness is actually proven.
- **Layer 2** proves the wiring: real browser → real assertion → real minted
  session → gated page. One happy path + the UV negative is enough; don't
  re-litigate the matrix in the browser.
- Every path the suite *doesn't* cover (native/desktop, cross-device hybrid,
  non-Chromium) is named, so green is never mistaken for total.
- Both enforcement points (`providers.py:123` and the native gate) are asserted,
  so the two-gate drift the build spec warns about is caught by a test, not a
  reviewer.

## What to avoid

- Mocking `py_webauthn` — then the test proves nothing about the security path.
- A browser-only suite — it can't run the negative matrix cheaply and it drags
  in Firebase for every case.
- Assuming localhost "just works" — without the origin/RP entry it will fail
  closed, and that failure looks like a code bug.
- Treating the native-code path as covered because the web e2e is green — it's a
  separate gate and needs its own integration test.

---

## Bead map

- **PABLO-egm.6** — API integration tests (the enforcement matrix above).
- **PABLO-egm.7** — browser e2e (CDP virtual authenticator, enroll + login).
- Both depend on **PABLO-egm.1** (endpoints); .7 also on **PABLO-egm.2**
  (frontend enroll/login surfaces).
