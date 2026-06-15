# Passkey Auth — Build Spec (PABLO-egm.1)

> **Implementation reference** for the WebAuthn passkey backend (epic
> **PABLO-egm**, backend slice **PABLO-egm.1**). Produced by a multi-agent spec
> pass plus an adversarial enforcement-airtightness review. Pairs with the
> security-review checklist this work creates
> (`docs/security/webauthn-security-review-checklist.md`) and the build roadmap
> (`docs/internal/passkey-auth-build-roadmap.md`).
>
> **Status notes (as of 2026-06-15):**
> - **Phase .1 is shipped** (pablo#470): two tables —
>   `platform.passkey_credentials` + `platform.passkey_challenges`. It does
>   **not** reuse `companion_devices`. The migration is
>   `9f4c1a7b2e60_passkey_credentials.py` (`down_revision = "d4b7e9a1c305"`).
> - The **IAP MFA-skip path** the review flagged is **resolved**: the unused IAP
>   auth mode was removed entirely (pablo#472). The findings below that reference
>   `auth_mode == "iap"` are kept verbatim as the historical record of why it had
>   to go — there is no longer an `auth_mode` setting or an `iap.py`.
> - Auth model is Firebase (the default provider) + pluggable OIDC (self-host).
>   The custom-claim enforcement seam described here applies to any provider via
>   the `amr` / `mfa_satisfied` path in `providers.py`.
> - Add **security-event logging** (passkey enroll/use, MFA outcomes, token
>   events) to the operational security log stream, **not** the PHI audit
>   (which is record-level and WORM-retained).

---

# Passkey Authentication — Implementation Spec (Phases .1 + .2) + Enforcement-Hardening List

**Status:** build-ready (.1 shipped; .2 next). **License:** AGPL-3.0. **Locked decision:** stay on Firebase as the *first-factor / session* layer; verify the WebAuthn assertion ourselves and mint a Firebase custom token carrying our own factor claim. **Not** migrating to a separate identity server. Libraries: `py_webauthn` (backend), `@simplewebauthn/browser` (frontend). RP origins: `app.pablo.health` (prod), `dev.pablo.health` (dev).

---

## The one fact that shapes everything

There is exactly **one** MFA enforcement read in the codebase plus **one** parallel hand-rolled copy:

1. `backend/app/auth/providers.py:123` — `mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor"))`, consumed at `service.py:372` (`require_mfa`).
2. `backend/app/routes/auth.py` (native code path) — a *separate* inline check: `if not firebase_claims.get("sign_in_second_factor"): raise ForbiddenError(...MFA_REQUIRED)`.

`firebase.sign_in_second_factor` is a **Firebase-reserved claim**, populated only by Identity Platform's own MFA flow (`mfaEnrollment:finalize`). `firebase_admin.auth.set_custom_user_claims()` / `create_custom_token(additional_claims=…)` **silently drop** anything in the `firebase.*` namespace. **A minted custom token cannot carry `sign_in_second_factor`.** Therefore the passkey factor must surface through a **custom application claim we both mint and read** — modeled on how `OidcVerifier` already derives `mfa_satisfied` from RFC 8176 `amr` (`providers.py:213-222`). Both enforcement points above must be taught the new claim, or the desktop companion login bypasses passkey.

---

## Phase .1 — Credential model + storage (shipped: pablo#470)

### Model: `PasskeyCredentialRow`

In `backend/app/db/platform_models.py`. `PlatformBase`-derived, `platform` schema (shared, **no RLS**), styled on `CompanionDeviceRow`. Multiple rows per user (one per authenticator).

**Critical rule (the `LaunchIntentRow` lesson):** `PlatformBase.metadata.create_all` runs at alembic env bootstrap *before* migrations, while `users.id` may be transiently `varchar`. So `user_id` is declared as a **bare column with no ORM `ForeignKey`** — the FK is added **in the migration only**.

| Column | Type | Null | Notes |
|---|---|---|---|
| `credential_id` | `String(255)` **PK** | no | WebAuthn credential ID, base64url text. Natural lookup key from the assertion (`response.id`); globally unique per spec → PK directly (mirrors `companion_devices.install_id`). |
| `user_id` | `Uuid(as_uuid=False)`, `index=True` | no | **Bare, no ORM FK.** FK to `platform.users(id) ON DELETE CASCADE` declared in the migration SQL only. |
| `public_key` | `LargeBinary` | no | COSE-encoded public key bytes from `py_webauthn` registration verify (`credential_public_key`). Stored as bytes (library consumes COSE on assert), not JWK — diverges from companion's JWK-as-JSONB by design. |
| `sign_count` | `BigInteger`, `server_default="0"` | no | Authenticator signature counter; clone-detection on assert. Platform authenticators legitimately report/stay 0. |
| `transports` | `JSONB` | yes | AuthenticatorTransport hints (`["internal","hybrid"]`) fed back into `allowCredentials`. Clients may omit. |
| `aaguid` | `String(36)` | yes | Authenticator model GUID. Nullable so privacy-preserving all-zero AAGUID can be stored as NULL rather than the zero sentinel. |
| `backup_eligible` | `Boolean`, `server_default="false"` | no | BE flag — is this a syncable (multi-device) passkey vs device-bound. Drives "no recoverable factor" UX. |
| `backup_state` | `Boolean`, `server_default="false"` | no | BS flag — currently synced. BE/BS transition monitoring is a security-review item. |
| `device_label` | `String(120)` | yes | User-supplied friendly name. No PHI; UI default derived from AAGUID/transports. |
| `created_at` | `DateTime(timezone=True)` | no | Registration timestamp. |
| `last_used_at` | `DateTime(timezone=True)` | yes | Last successful assertion; NULL until first use. |
| `revoked_at` | `DateTime(timezone=True)` | yes | Soft-revoke. Assertion path filters `revoked_at IS NULL` (mirrors `companion_devices.revoked_at`). |

**Indexes:** PK on `credential_id`; `ix_passkey_credentials_user_id` on `user_id` (list-a-user's-passkeys + `allowCredentials` assembly).

**Docstring:** states **no PHI** (all authenticator metadata + a user-chosen label) and that it lives in the shared `platform` schema (no RLS), same scope as `companion_devices`. AGPL header; a `PABLO-` bead id in the docstring is acceptable (companion/launch migrations carry them).

**Relationship to `UserIdentityRow`:** complementary, not overlapping — same split as `user_identities` ↔ `companion_devices`. `UserIdentityRow` maps `(provider, subject_id) → user_id` (who you are to an issuer); `PasskeyCredentialRow` stores a *factor* bound to `user_id` (a thing you have). The passkey is not a new login provider; the user's provider identity stays Firebase.

### Challenge store: `PasskeyChallengeRow`

Modeled byte-for-byte on `LaunchIntentRow` (the single-use/consume precedent). Same bare-`user_id` FK rule.

| Column | Type | Null | Notes |
|---|---|---|---|
| `challenge_hash` | `String(64)` **PK** | no | SHA-256 of the CSPRNG challenge. **Store the hash, never the raw challenge** (it leaves the server once, in the begin response). Lookup key on finish. |
| `ceremony` | `String(16)` | no | `register` \| `authenticate`. CHECK constraint in migration. |
| `user_id` | `Uuid(as_uuid=False)`, `index=True` | yes | Bound to the enrolling/asserting user. **Nullable** — usernameless (resident-key) authenticate-begin has no user yet. FK in migration only. |
| `created_at` | `DateTime(timezone=True)` | no | |
| `expires_at` | `DateTime(timezone=True)`, `index=True` | no | Authoritative ≤300s expiry; re-checked server-side on finish. Match `LaunchIntentRow`'s short-TTL order. |
| `consumed_at` | `DateTime(timezone=True)` | yes | Non-null = spent (single-use). |

A periodic sweep / TTL backstop reclaims rows (reuse whatever reclaims `launch_intents`).

### Migration (Phase .1) — as shipped

- **Chain:** single linear chain at `backend/alembic/versions/`.
- **Shipped revision:** `9f4c1a7b2e60_passkey_credentials.py`, `down_revision = "d4b7e9a1c305"` (the `launch_intents` revision was the real single head — verify the head with `alembic heads` against your checkout's `origin/main`, not a dirty working tree).
- **Style:** raw `op.execute("CREATE TABLE IF NOT EXISTS platform.passkey_credentials (...)")` + `CREATE INDEX IF NOT EXISTS`, following the `a8f3c7d2b916_companion_devices_table.py` pattern. The `user_id` FK (`REFERENCES platform.users(id) ON DELETE CASCADE`) is declared inside the CREATE TABLE in the migration (the ORM omits it). `CHECK` constraints for `ceremony` and `sign_count >= 0`.
- `downgrade()` = `DROP TABLE IF EXISTS platform.passkey_challenges; DROP TABLE IF EXISTS platform.passkey_credentials`.
- **Header:** AGPL-3.0 copyright line + docstring; `PABLO-` bead id allowed in docstring.
- **Scope:** platform-schema (shared, no-RLS). Does **not** touch the per-tenant template or any `practice_*` schema; **no tenant fan-out.**

---

## Phase .2 — Endpoints + custom-token mint + enforcement integration (PABLO-egm.1, next)

### Dependencies / plumbing

- Pin `py_webauthn` in `pyproject.toml` via **poetry** (never uv), with a same-PR lockfile update.
- New router `backend/app/routes/passkey.py`, registered in `main.py` alongside the auth router family (mirror `routes/ext_auth.py` / `routes/auth.py` registration).
- New settings: `webauthn_rp_id`, `webauthn_rp_name`, `webauthn_origins` (list: prod + dev). RP id = registrable domain (`pablo.health`); origins are the full `https://…` values.

### Endpoint contracts

Begin/finish split so the challenge lives server-side between calls.

**1. `POST /api/auth/passkey/register/begin`**
- Posture: `Depends(get_current_user_no_mfa)` — enrollment is a step-up on an existing identity (see hardening H4 for the must-be-MFA'd-first variant).
- Request: empty (identity from bearer).
- Response `PasskeyRegistrationOptions`: serialized `generate_registration_options(...)` — `rp.id = settings.webauthn_rp_id`; `user.id` = the **opaque pablo user id** (not email/PHI); `user.name`/`displayName` = account email; `excludeCredentials` = user's existing credential ids (blocks double-enroll); `authenticatorSelection.userVerification = "required"`, `residentKey = "preferred"`; `attestation = "none"` (no attestation-CA verification in scope — see OPEN QUESTION 4).
- Side effect: persist `PasskeyChallengeRow(ceremony="register", user_id=…)`.

**2. `POST /api/auth/passkey/register/finish`**
- Posture: same `get_current_user_no_mfa` as begin (same user).
- Request `PasskeyRegistrationVerify`: `{ "credential": <RegistrationResponseJSON> }`.
- Logic: (1) look up pending challenge by SHA-256(challenge-from-clientDataJSON), ceremony=`register`, bound to this `user_id`; reject unknown/consumed/expired → `400`. (2) `verify_registration_response(..., require_user_verification=True)`. (3) persist `PasskeyCredentialRow`; mark challenge `consumed_at`. (4) audit via `backend.app.services.audit_service` (credential enrolled, identifier only, no PHI).
- Response: `{ "credential_id": "...", "created_at": ... }` (201).

**3. `POST /api/auth/passkey/authenticate/begin`**
- Posture: `Depends(truly_public)` — this *is* the factor being asserted. (Any new `truly_public` route must be justified in the PR per `route_security.py:60`.)
- Request `PasskeyAuthenticationBegin`: `{ "user_handle"?: ... }` optional (usernameless with resident keys). For the step-up flow the begin call may carry the first-factor bearer so the challenge binds to that session.
- Response `PasskeyAuthenticationOptions`: serialized `generate_authentication_options(rp_id, allow_credentials, user_verification="required")`.
- Side effect: persist `PasskeyChallengeRow(ceremony="authenticate", user_id=… or NULL)`.

**4. `POST /api/auth/passkey/authenticate/finish` — verify + custom-token mint**
- Posture: `Depends(truly_public)` + `Depends(require_rate_limit)` (mirror `routes/auth.py` native-code path). **This is a new pre-auth, token-issuing surface — treat it as the most dangerous route in the system.**
- Request `PasskeyAuthenticationVerify`: `{ "credential": <AuthenticationResponseJSON> }`.
- Logic:
  1. Look up pending challenge by SHA-256(challenge), ceremony=`authenticate`; reject unknown/consumed/expired → `400`. **Consume it before issuing anything** (single-use).
  2. Resolve `PasskeyCredentialRow` by `credential.id`, `revoked_at IS NULL`; else `401`.
  3. `verify_authentication_response(..., require_user_verification=True, credential_public_key=row.public_key, credential_current_sign_count=row.sign_count)`.
  4. **Clone/counter check:** if `new_sign_count <= stored sign_count` *and* not the legitimate-0 case → reject + audit (see hardening H7).
  5. On success: update `sign_count`, `last_used_at`; `create_custom_token(uid, additional_claims={"pablo_amr": ["webauthn"]})`. Audit (assertion success, identifier only).
  6. **The `pablo_amr` claim is stamped ONLY here, only after a verified assertion, in the same request.** Never client-influenced; never copied forward across any other mint.
- Response: `{ "custom_token": "<firebase custom token>" }`. Frontend calls `signInWithCustomToken()` to obtain a Firebase ID token that now carries `pablo_amr`.

### Enforcement integration (the load-bearing part)

Teach **both** enforcement points to honor the passkey claim:

1. **`providers.py` (the seam):** extend `FirebaseVerifier.verify_from_decoded` (line ~123) to:
   ```
   mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor")) or _passkey_factor_satisfied(claims)
   ```
   where `_passkey_factor_satisfied(claims)` = `"webauthn" in claims.get("pablo_amr", [])` — mirroring `_oidc_mfa_satisfied` (line 213). This is the single seam `require_mfa` (`service.py:372`) reads.
2. **`routes/auth.py` native-code gate:** the inline `firebase_claims.get("sign_in_second_factor")` check is a **parallel** path that does NOT go through the verifier. Replace its hand-rolled condition with a call to the same `_passkey_factor_satisfied` helper (OR with the existing native check), or it silently stays first-factor-only after cutover. **This is HARDENING item H3 — do not skip it.**

Frontend (`@simplewebauthn/browser`): begin → `startRegistration`/`startAuthentication` → finish. On authenticate-finish, exchange `custom_token` via `signInWithCustomToken`. Check the `frontend/overlay` tree before editing any frontend auth file (overlay file-replacement can shadow in-tree fixes).

---

## ENFORCEMENT-HARDENING LIST — backdoors to close (prioritized, deduped)

Default posture: a gap = a live bypass. The whole surface pivots on the custom-token mint, because a session from `signInWithCustomToken()` carries **no** `sign_in_second_factor` — so every first-factor-only path that reaches a token-issuance seam without touching WebAuthn is a candidate bypass.

### CRITICAL — ship-blockers

- **H1 — The mint endpoint must stamp `pablo_amr:["webauthn"]` ONLY after a fresh, verified `py_webauthn` assertion, in the same request.** Never derive it from a first-factor token, never accept a client-supplied claim, never copy it forward across any other token mint. This is *the* gate; if it leaks, the entire factor is cosmetic. (Combines the two failure modes: keeping `firebase.*` reserved-claim reads → every passkey user locked out → pressure to relax the gate = backdoor; vs. a custom claim stamped anywhere but post-assertion = backdoor.)
- **H2 — Password-reset / first-factor-only must NOT reach a passkey-satisfied token.** After a Firebase password reset the user holds a fresh first-factor token. If the mint (or any "bootstrap/recovery" path) accepts "valid first-factor token → mint with `pablo_amr`," then password reset alone inherits the second factor — the canonical PHI bypass. A user with **no passkey enrolled** routes to an **enroll-only, PHI-denied** session (a posture *below* `get_current_user_no_mfa`), never a passkey-satisfied token. Enrollment itself is gated behind the existing second factor or an out-of-band step (see H4).
- **H3 — The `routes/auth.py` native-code MFA check is a second, parallel enforcement point.** It reads `sign_in_second_factor` inline and does not use the verifier seam. It must be taught `pablo_amr` too, or the desktop companion login stays first-factor-only after cutover. Fix both `providers.py:123` *and* this inline check in the same PR.
- **H4 — Enrollment gating.** `register/begin|finish` run on `get_current_user_no_mfa` today, but enrolling a *first* second factor from a first-factor-only session is itself a step-up: require the existing second factor (or an out-of-band verification) before binding a new passkey, so a phished first factor can't self-enroll a passkey and bootstrap MFA. Resolve OPEN QUESTION 1 before coding.

### HIGH

- **H5 — Single-use challenge, bound + short-TTL.** Store SHA-256(challenge) only; consume (`consumed_at`) before issuing any token; re-check `expires_at` server-side; bind register challenges to `user_id` and authenticate challenges to the begin session when present. Reject unknown/consumed/expired → `400`. (Prevents replay of a captured assertion, especially post-reset.)
- **H6 — Verify origin + RP id on every ceremony.** `expected_origin ∈ {app.pablo.health, dev.pablo.health}`, `expected_rp_id = pablo.health`, `require_user_verification=True` on both register and authenticate verify. UV-required is what makes the passkey a *second factor* rather than just possession.
- **H7 — Sign-count clone detection.** Reject `new_sign_count <= stored` (except the legitimate platform-authenticator 0/0 case); audit the anomaly. Update `sign_count` only on success.
- **H8 — Custom-token blast radius.** `create_custom_token` is a brand-new minting capability (none exists today). Constrain it to the single mint endpoint; never expose a general "mint a token for uid" helper. Short token TTL; the `pablo_amr` claim must not survive a Firebase token refresh unless re-asserted (verify Firebase's refresh behavior — OPEN QUESTION 2).

### MEDIUM

- **H9 — Rate-limit + audit the mint and both begin endpoints** (mirror `require_rate_limit` on the native path). Audit register/finish, authenticate/finish (success + clone-reject) via `audit_service`, identifiers only, no PHI.
- **H10 — BE/BS transition monitoring.** A `backup_eligible=false → true` flip is a spec violation (flag/alert); `backup_state` toggling on a BE-true credential is normal sync (allow). Store both flags at registration; re-evaluate on assertion.
- **H11 — `excludeCredentials` on register** to block double-enrollment of one authenticator; **`allowCredentials` from `transports`** on authenticate for UX, but never trust transports as a security control.
- **H12 — Revocation is honored on the hot path.** Assertion lookup filters `revoked_at IS NULL`; soft-revoke keeps the audit trail of removed factors.

---

## Framing notes (apply to all .1/.2 artifacts)

- AGPL-3.0 header on new files.
- Account email is framed as "account email."
- Imports resolve against `backend.app.*`. Type-annotate everything; `str | None` not `Optional`.
- Run `make check` (lint **incl. mypy** + test) before declaring CI-green — `pytest` + `ruff` alone is not sufficient.
- Create the two referenced docs as part of this work: `docs/security/webauthn-security-review-checklist.md` (the H-list above) and `docs/internal/passkey-auth-build-roadmap.md`.

---

## OPEN QUESTIONS (need a human decision before coding)

1. **Enrollment gate (H4):** must a user already have a second factor (or pass an out-of-band step) before enrolling their *first* passkey? Bootstrapping MFA from a first-factor-only session is the chicken-and-egg risk. Options: (a) require existing Firebase native MFA first; (b) email-link out-of-band confirm; (c) accept first-factor enroll-only for the very first credential with extra audit + a hard cap. This directly determines whether H2/H4 are airtight.
2. **Claim survival across refresh (H8):** does `pablo_amr` set via `create_custom_token` persist when the Firebase session refreshes its ID token, or must we re-assert? If it persists indefinitely, define the re-assertion / max-session policy. Needs an empirical check against Identity Platform.
3. **Recovery / lost-authenticator policy:** what's the supported path when a user loses their only passkey? This is where H2 bypasses get reintroduced as "support workflows." Must be designed up front (e.g. require a second enrolled credential, or an admin out-of-band reset that itself does not mint a passkey-satisfied token).
4. **Attestation policy:** spec assumes `attestation="none"` (no authenticator-model allowlist / metadata-service verification). If any compliance requirement needs attested authenticator provenance, that's additional scope (FIDO MDS integration) — confirm "none" is acceptable.
5. **Custom-token mint precedent:** none exists in the tree (`create_custom_token` appears nowhere). Confirm there's no policy objection to introducing Firebase custom-token minting at all, and that the service account in use has the `iam.serviceAccountTokenCreator` capability required to sign custom tokens.

**Key file paths:** model/challenge → `backend/app/db/platform_models.py`; migration → `backend/alembic/versions/` (shipped: `9f4c1a7b2e60`, down_revision `d4b7e9a1c305`); router → `backend/app/routes/passkey.py` (register in `main.py`); enforcement → `backend/app/auth/providers.py:123` + `backend/app/routes/auth.py` (inline native-code MFA check); precedent migration → `a8f3c7d2b916_companion_devices_table.py`.

---

## Appendix — adversarial backdoor findings (raw)

From the parallel-skeptic pass; kept verbatim as input for the **PABLO-egm.5** independent security audit. Note: findings that reference `auth_mode == "iap"` predate pablo#472, which removed the IAP auth mode and the dead `iap.py` verifier entirely — they are retained as the rationale for that removal, not as live gaps.


### Skeptic 1

I have everything I need. Let me write up the findings grounded in the real code.

## ADVERSARIAL FINDINGS — passkey first-factor / reset bypass surface

Default posture applied: where the design leaves a gap, I treat it as a live bypass. All cites are `backend/app/...`.

The single fact the whole analysis pivots on: there is exactly **one** MFA enforcement read in the codebase — `providers.py:123`, `mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor"))`, consumed once at `service.py:372`. That claim (`firebase.sign_in_second_factor`) is **set by Firebase only when a session is established through Firebase's own native MFA challenge** (TOTP/SMS second factor). The locked design replaces that with "verify WebAuthn backend-side, then mint a Firebase custom token." A session created via `signInWithCustomToken()` does **not** carry `sign_in_second_factor`. So the entire passkey factor lives or dies on what the custom-token-mint path stamps onto the token — and several first-factor-only paths reach the same token-issuance seam without ever touching WebAuthn.

---

### FINDING 1 — CRITICAL: a plain Firebase password sign-in yields a token that the *new* passkey gate can't distinguish from a passkey'd one (and worse, may auto-pass)

**Path:** Firebase email/password (or email-link, or Google) first-factor sign-in → client holds a valid Firebase ID token → `require_mfa` (`service.py:331`) → `mfa_satisfied` from `providers.py:123`.

**Why it skips passkey:** Today `sign_in_second_factor` is present only after Firebase's native MFA. Once you move the second factor *out* of Firebase (custom token mint), you must define what makes `mfa_satisfied == True`. Two failure modes, both default-to-backdoor:

- **1a.** If the design keeps reading `firebase.sign_in_second_factor` and arranges for the custom token to carry it, note that **custom tokens cannot set the `firebase.*` reserved claim block** — `firebase.sign_in_second_factor` is reserved and stamped by Firebase's sign-in machinery, not by `additional_claims` in `create_custom_token`. So the passkey'd session will have `mfa_satisfied = False` and **every passkey user is locked out**, pressuring a "temporary" relaxation of the gate. That relaxation is the backdoor.
- **1b.** If the design instead introduces a *custom* claim (e.g. `passkey_verified`) and makes `providers.py:123` read `mfa_satisfied = bool(claims.get("passkey_verified")) or bool(firebase_claims.get("sign_in_second_factor"))`, then a plain password session that never did WebAuthn has neither claim → correctly blocked. **But** any place that mints a custom token (re-auth, account recovery, "remember this device") that sets `passkey_verified` without re-running the assertion re-opens it. See Finding 2.

**Severity:** CRITICAL — this is the load-bearing gate.

**Fix:** The passkey factor must be asserted by a **server-minted claim that is only ever stamped immediately after a successful `py_webauthn` assertion verification**, inside the same request that verified the assertion. Add a `PasskeyVerifier`-equivalent contribution to `mfa_satisfied` at the single seam (`providers.py:123`): `mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor")) or _passkey_factor_satisfied(claims)`, where `_passkey_factor_satisfied` reads a custom claim (`amr`-style, e.g. `"webauthn" in claims.get("amr", [])`) that is **only** written by the mint endpoint after assertion success. Never let the client influence that claim; never copy it forward across a token mint that didn't re-verify.

---

### FINDING 2 — CRITICAL: the custom-token mint endpoint is the new `truly_public` pre-auth surface — if it stamps the passkey claim from anything but a fresh assertion, it IS the password-reset bypass

**Path (by analogy to the existing pre-auth surface):** `routes/auth.py:79-123` (`/api/auth/native/code`) is the template — `Depends(truly_public)` (`route_security.py:43`), no `require_mfa`, hand-rolls its own MFA check at `routes/auth.py:112-115`. The new passkey mint endpoint will be the same shape: public, pre-JWT, verifies something, then issues a token.

**Why it skips passkey:** The "password-reset / first-factor-only" angle concretely: after a Firebase password reset, the user holds a fresh **first-factor** Firebase ID token (`auth_time` just now, no second factor). If the mint endpoint accepts "valid first-factor Firebase token → mint custom token with passkey claim" as a recovery/bootstrap path (e.g. "no passkey enrolled yet, let them in to enroll"), then **password reset alone reaches a passkey-satisfied token without any assertion.** That is the canonical first-factor-only PHI path. An attacker who phishes/resets the Firebase password (first factor) inherits the second factor for free.

**Severity:** CRITICAL.

**Fix:** The mint endpoint must (a) require a fully-verified WebAuthn assertion in the *same* request before stamping the passkey claim — never "first-factor token is enough"; (b) mirror `routes/auth.py:112-115`'s explicit gate but for the assertion, not for `sign_in_second_factor`; (c) treat "user has no passkey enrolled" as a route to an **enroll-only, PHI-denied** session (a posture below `get_current_user_no_mfa`, not a passkey-satisfied token). Enrollment must itself be gated behind the existing second factor or an out-of-band step, never behind first-factor-only. Bind the assertion `challenge` server-side (one-time, short TTL) so a replayed/captured assertion can't be re-spent post-reset.

---

### FINDING 3 — HIGH: `routes/auth.py:110-115` native-code gate is a *parallel* hand-rolled MFA check that does NOT use the verifier seam — it will silently stay first-factor-only after the passkey cutover

**Path:** `routes/auth.py:112-115` (inline, reads `firebase_claims.get("sign_in_second_factor")` directly and raises `MFA_REQUIRED`).

**Why it skips passkey:** This endpoint mints the auth code the **native/desktop app** exchanges for the long-lived tokens it uses against PHI (`/native/exchange`, `routes/auth.py:126`). It does NOT call `verify_token`/`require_mfa`/the `mfa_satisfied` seam — it reads `sign_in_second_factor` directly. Once the second factor moves to passkey (custom token, no `sign_in_second_factor`), this check **fails closed for legitimate passkey users** (locking out desktop), which forces someone to weaken it; OR if it's "fixed" by deleting the check, the native app gets a PHI-grade token with **no second factor at all**. Either way the native path diverges from the web path's enforcement.

**Severity:** HIGH (native/desktop is a full PHI client).

**Fix:** Route this endpoint through the same `mfa_satisfied`-derived helper as `require_mfa` rather than reading `sign_in_second_factor` inline. Extract the boolean (`def _mfa_satisfied_for(decoded) -> bool`) used by both `providers.py:123` and here, so there is exactly one definition of "second factor present." When passkey lands, both update together. Add this endpoint to `tests/test_route_mfa_guardrails.py`'s reasoning so a reviewer sees the native path classified.

---

### FINDING 4 — HIGH: `get_current_user_id` (`service.py:292`) is token-valid-only — no `require_mfa`, no `enforce_idle_session` — so any PHI route hung off it is passkey-exempt by construction

**Path:** `service.py:292-328`. It calls `_verify_request_identity` (`service.py:318`) directly and resolves the pablo user id. It does **not** depend on `require_mfa` or `enforce_idle_session`. The docstring/enforcement map even flags it: "authenticated but neither MFA- nor idle-gated."

**Why it skips passkey:** A valid *first-factor* Firebase token satisfies it completely. No second factor (Firebase native today, passkey tomorrow) is ever consulted. Any current or future route using `Depends(get_current_user_id)` reaches its handler with a first-factor-only identity. I confirmed no PHI route currently uses it (`grep` for `Depends(get_current_user_id)` in `routes/` returned nothing), but it's exported (`auth/__init__.py:6,13`) and the guardrail test classifies a route as MFA-required by *transitive* `require_mfa` — `get_current_user_id` does not contribute that, so a route could use it and still "classify."

**Severity:** HIGH (latent; one import away from a live first-factor PHI path).

**Fix:** Either (a) make `get_current_user_id` depend on `enforce_idle_session` + the MFA gate like its siblings, or (b) if it must stay lightweight, rename it to signal the posture (`get_current_user_id_unverified_mfa`) and add an explicit assertion in `test_route_mfa_guardrails.py` that **no route** may use it as its terminal auth dependency. Don't leave a token-valid-only dep in the same namespace as the gated ones during a passkey cutover.

---

### FINDING 5 — HIGH: `require_mfa == False` and `is_development` each short-circuit the gate *before* `mfa_satisfied` is read — passkey inherits both skips

**Path:** `service.py` — `require_mfa` returns the token *unchecked* if `not settings.require_mfa` or if `settings.is_development`. The `mfa_satisfied` read is never reached in those modes. Plus the E2E bypass nearby. (The historical `auth_mode == "iap"` early-return that also lived here was removed in pablo#472.)

**Why it skips passkey:** These are the same skips MFA has today, but the threat model changes when the second factor moves out of Firebase. The `is_development` skip is the dangerous one operationally: if a prod-like environment is ever misclassified as development (it's a derived property), passkey enforcement vanishes with zero signal. Default-to-backdoor: assume one of these will be misconfigured.

**Severity:** HIGH (configuration-dependent, but the blast radius is "passkey fully off").

**Fix:** (a) Make `require_mfa=False` log a loud startup warning the same way admin-dev-bypass does (`main.py`). (b) For the E2E bypass, it's already correctly gated on `not settings.is_prod_project` — keep that invariant when adding the passkey path; do **not** add a passkey-specific test bypass that lacks the `is_prod_project` guard.

---

### FINDING 6 — MEDIUM: idle-session anchor (`auth_time`) on a custom-token session is the mint time, not the original sign-in — lets a first-factor session "refresh" its way past the passkey requirement indefinitely

**Path:** `idle_session.py:109-110` anchors the idle clock on `auth_time` (Firebase) / `sub`. `enforce_idle_session` (`service.py:387`) runs after `require_mfa`.

**Why it relates to passkey:** Custom-token sign-in sets `auth_time` to the moment of `signInWithCustomToken`. If the passkey claim is ever carried across a token *refresh* (Firebase refresh tokens last ~30 days, called out in `idle_session.py:6`), a session that did one passkey assertion can ride refreshes for a month, and a session that did *none* (first-factor custom token from a reset, Finding 2) gets a fresh `auth_time` that looks like a legitimate new auth event — masking the absence of an assertion. The idle gate can't tell "passkey'd 29 days ago" from "first-factor reset 1 minute ago."

**Severity:** MEDIUM (depends on Findings 1/2 being fixed; this is the defense-in-depth backstop).

**Fix:** Stamp an explicit `passkey_auth_time` (or reuse `auth_time` semantics) into the custom token only at assertion time, and have the idle/MFA path require the passkey claim to be *fresh relative to the current sign-in event*, not merely present. Re-mint (and thus re-assert) on a cadence shorter than the refresh-token lifetime for PHI access. Don't let the passkey claim survive a refresh that didn't re-verify.

---

### FINDING 7 — MEDIUM: middleware swallows verification errors and primes `request.state.verified_identity` — a custom-claim passkey signal placed there by a non-WebAuthn path would be trusted downstream

**Path:** `middleware.py:61-65` stashes `verified_identity`/`decoded_firebase_token` on `request.state`; `_verify_request_identity` (`service.py:253-257`) and `_identity_for_decoded` (`service.py:285-288`) **return the stashed identity without re-verifying** when the token matches. Errors are swallowed (`middleware.py:72-73`).

**Why it relates to passkey:** The `mfa_satisfied` value is computed once (in the verifier) and cached on `request.state`. This is fine *as long as the only producer is the verifier reading a Firebase/OIDC-signed claim.* The risk in the new design: if the passkey mint flow writes any auth state onto `request.state` or onto a claim the verifier trusts, it must come from a cryptographically verified token, never from request-scoped scratch state. The middleware's swallow-and-prime pattern means a malformed/forged token doesn't get rejected here — it just doesn't prime — so the trust boundary is entirely the verifier. Keep it that way.

**Severity:** MEDIUM (architectural guardrail, not a live hole today).

**Fix:** Ensure `mfa_satisfied` / the passkey claim is **only ever** populated inside a `*Verifier.verify_from_decoded` from a signed claim. Add a test asserting `VerifiedIdentity.mfa_satisfied` can't be set true from a token lacking both `firebase.sign_in_second_factor` and the signed passkey claim. Do not read passkey state from `request.state` in any auth dependency.

---

### Cross-cutting fix (the one that closes the class)

There must be exactly **one** function that answers "did this identity satisfy the second factor," consumed by `providers.py:123`, `service.py:372`, AND `routes/auth.py:112-115`. Today the native path forks (Finding 3) and `get_current_user_id` opts out (Finding 4). Centralize it, make the passkey contribution come only from a signed claim stamped at assertion time (Findings 1, 2, 7), and add it to the four-posture guardrail (`route_security.py`, `tests/test_route_mfa_guardrails.py`) so the test fails if any route reaches PHI without transiting it. Describe it as "a second authentication factor / WebAuthn assertion."

Key files for the implementer: `backend/app/auth/providers.py:123` (the seam to extend), `auth/service.py:331-384` (the gate + its skips), `auth/service.py:292-328` (the opt-out dep), `routes/auth.py:79-156` (the public mint-endpoint template + the forked gate at `:112-115`), `auth/idle_session.py:109` (the freshness anchor), `db/middleware.py:61-73` (the trust-boundary caching).

### Skeptic 2

I have the complete picture. Here are the concrete findings.

---

# ADVERSARIAL REVIEW: Paths to PHI that skip the passkey check

The passkey design (locked) lands a verified WebAuthn assertion as `mfa_satisfied=True` through the `providers.py` contract — i.e. the boolean is consumed at exactly **one** enforcement point: `service.py:372` inside `require_mfa`. Everything below is a path that reaches PHI (or a PHI-adjacent write) **without that boolean ever being read**, or where the boolean is satisfied by something other than a real passkey assertion. All cites are `backend/app/...`.

---

## F1 — (HISTORICAL — removed in pablo#472) `auth_mode == "iap"` returned the token UNVERIFIED, and the IAP JWT was never checked anywhere

**Path (pre-pablo#472):** `require_mfa` early-returned `decoded_token` when `settings.auth_mode == "iap"`, skipping the `mfa_satisfied` read entirely. The stated justification was "IAP verifies the user at the load balancer." **But `verify_iap_jwt` (`auth/iap.py`) had zero callers in the request path** — confirmed by grep at the time: the only references to it were its own definition. There was no IAP middleware, no `Depends(verify_iap_jwt)`, nothing reading `X-Goog-IAP-JWT-Assertion`. So in IAP mode the *only* thing standing between a request and PHI was the Firebase bearer token in `require_mfa` → which then early-returned and never checked the second factor.

- **Why it skipped the passkey:** the gate returned before `if not identity.mfa_satisfied`. A passkey assertion was irrelevant in IAP mode.
- **Why it was a real hole, not just a config posture:** the in-app defense-in-depth that the `iap.py` docstring promised ("verifying the IAP-signed JWT header on each request") was never wired.
- **Resolution:** the `auth_mode` setting, the `iap` branch in `require_mfa`, and the dead `iap.py` verifier were all deleted (pablo#472). There is no longer a posture that early-returns out of `require_mfa` for IAP. The passkey checklist retains the principle: "no posture may early-return out of `require_mfa` without an enforced equivalent factor."

---

## F2 — Custom-token mint will silently produce `mfa_satisfied=False`; the WebAuthn assertion never reaches the only gate (CRITICAL — design defect to avoid)

**Path:** The locked design = "Firebase = first-factor/session only; we verify the WebAuthn assertion then mint a Firebase custom token." But `FirebaseVerifier.verify_from_decoded` (`providers.py:123`) derives the factor **solely** from `mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor"))`.

`sign_in_second_factor` is populated by Firebase **only** for Firebase's *native* MFA (TOTP/SMS multi-factor sign-in). A token minted from `firebase_auth.create_custom_token(uid, additional_claims=...)` and exchanged for an ID token **does not carry `firebase.sign_in_second_factor`** — that subtree is Firebase-internal and not settable via custom claims. So a passkey-authenticated session, exchanged the obvious way, yields `mfa_satisfied=False` and is *rejected* by `require_mfa` — OR, worse, the implementer "fixes" the rejection by stuffing a top-level custom claim that the verifier doesn't read, leaving the door to be propped open later.

- **Why it skips the passkey:** the single producer (`providers.py:123`) reads a claim the passkey path cannot set. The verifier has no `PasskeyVerifier`-equivalent branch.
- **Severity:** CRITICAL (correctness + security): the naive workaround is to weaken `require_mfa` (e.g. add an env flag, or trust an unverified custom claim), which is exactly a backdoor.
- **Fix:** Mint the custom token with a namespaced, server-controlled claim (e.g. `additional_claims={"pablo_mfa": "passkey"}`) and extend `FirebaseVerifier.verify_from_decoded` to OR it in: `mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor")) or decoded.get("pablo_mfa") in PABLO_MFA_FACTORS`. The claim must be set **only** server-side after a successful `py_webauthn` assertion verification (RP ID + origin + challenge + sign-count + the user's registered credential from the credential table) — never client-supplied, never settable through any client-facing custom-claims surface. Add a guardrail test that a token with `pablo_mfa` but no server-verified assertion can't be produced.

---

## F3 — E2E MFA bypass keys on `email_verified`, which Firebase signup does NOT verify (HIGH)

**Path:** `require_mfa` (`auth/service.py`): bypasses `mfa_satisfied` for any token whose email is in `E2E_TEST_EMAILS` and whose `email_verified` is true (guarded by `not settings.is_prod_project`). The `_resolve_user` path is careful to note Firebase signup *doesn't verify email* and therefore gates its e2e/pentest bypass on a `re.match` of a structured pattern. **This MFA bypass does not** — it trusts `email in settings.e2e_test_emails` plus a self-assertable `email_verified` claim.

- **Why it skips the passkey:** returns before the `mfa_satisfied` read.
- **Why it's exploitable:** the guard is `not settings.is_prod_project`, which is dev/staging — but the WebAuthn security checklist must hold across *all* non-prod RP origins (dev.pablo.health is a real PHI-touching environment with real test patients). An attacker who can register a Firebase account at one of the `E2E_TEST_EMAILS` addresses (signup doesn't verify the address; `email_verified` can ride from certain providers) skips MFA in dev. Defense-in-depth principle: don't stop at "prod is safe."
- **Severity:** HIGH (dev PHI exposure; weaker than the sibling `_resolve_user` pattern it should mirror).
- **Fix:** Gate this bypass on the same structured `E2E_EMAIL_PATTERN.match(email)` used by `_resolve_user`, not a settings-list membership + `email_verified`. Better: in the passkey world, drive E2E MFA satisfaction through a *real* minted passkey factor for the pinned test user, so the bypass branch can be deleted entirely. The checklist should forbid any MFA bypass keyed on a self-assertable claim.

---

## F4 — `is_development` blanket-skips `require_mfa` AND `enforce_idle_session` AND `require_admin` (MEDIUM, posture)

**Path:** `require_mfa` returns on `is_development`; `idle_session.check_and_touch` returns on `is_development`; `require_admin` returns on `is_development`. `is_development` = `environment == "development"`.

- **Why it skips the passkey:** three independent early-returns, none read `mfa_satisfied`.
- **Risk:** this is intentional for local dev, but it's a single env var (`ENVIRONMENT`) that, if ever set wrong on a deployed PHI instance, disables MFA + idle + admin gates simultaneously. Note `is_development` is *distinct* from `is_prod_project` — a non-prod-project deployment with `ENVIRONMENT=development` would be fully open. The passkey rollout adds a new factor but inherits this kill-switch.
- **Severity:** MEDIUM (config-dependent; the blast radius — three controls off one flag — is what makes it worth a checklist line).
- **Fix:** No code change strictly required, but the WebAuthn checklist should assert: (1) `is_development` is never true on any deployed environment that serves real PHI (add a startup assertion that refuses to boot if `is_development and is_prod_project`), and (2) document that `ENVIRONMENT=development` is a local-only setting.

---

## F5 — `get_current_user_id` is authenticated-but-not-MFA/idle-gated; any PHI route hanging off it bypasses the passkey (HIGH — latent)

**Path:** `get_current_user_id` (`service.py:292-328`) calls `_verify_request_identity` directly and resolves the pablo user id. It depends on **neither** `require_mfa` nor `enforce_idle_session`. A token-valid (single-factor, possibly idle) session satisfies it.

- Today grep shows it has no *route* callers (only the `auth/__init__.py` export and a doc mention), so it is not *currently* a live PHI path. But it is a public, exported dependency that *looks* interchangeable with `get_current_user`, and `route_security.py`'s four-posture classifier (#1 requires transitive `require_mfa`) would classify a `get_current_user_id`-only route as... unclassifiable → the guardrail test catches it. Good. **However**, the risk is a future PHI route author picking `get_current_user_id` for "I just need the user id" and the reviewer missing it because the name is so close to `get_current_user`.
- **Why it skips the passkey:** no `require_mfa` in its chain → `mfa_satisfied` never read.
- **Severity:** HIGH if ever used on a PHI route; LOW today (no callers).
- **Fix:** Either (a) route `get_current_user_id` through `enforce_idle_session`/`require_mfa` like its siblings, or (b) rename it to make the posture explicit (`get_current_user_id_unverified_mfa`) and add a guardrail test asserting it appears on **zero** routes (it's an internal helper). Confirm `test_route_mfa_guardrails.py` actually fails a `get_current_user_id`-only route (the four-posture set doesn't include it — verify it's not silently treated as posture #1).

---

## F6 — `routes/auth.py:112` is a SECOND, hand-rolled MFA gate that won't learn about passkeys (HIGH — must update in lockstep)

**Path:** the native desktop code-exchange flow re-implements the MFA check inline (`routes/auth.py:110-115`), reading `firebase_claims.get("sign_in_second_factor")` directly and raising `MFA_REQUIRED`. This does **not** call `verify_token`/`VerifiedIdentity`/`mfa_satisfied`. When you add the passkey factor (F2 fix), **this gate will keep rejecting passkey-authenticated native users** (their custom token has no `sign_in_second_factor`), *and* if someone "fixes" it independently they'll re-derive the factor logic in two places that can drift.

- **Why it matters for passkeys:** it's a parallel producer/consumer of the MFA decision that bypasses the single seam the whole design rests on. The endpoint hands back the real `id_token` + `refresh_token` (`/native/exchange`), so getting the factor logic wrong here either locks out passkey users or (if loosened) mints session tokens for single-factor users.
- **Severity:** HIGH (correctness + the drift is exactly the "parallel gate" anti-pattern).
- **Fix:** Replace the inline check with the shared seam: build the `VerifiedIdentity` via `verify_token(request.id_token)` and reuse `require_mfa`'s decision (extract the gate logic in `service.py` into a pure `_mfa_satisfied_or_raise(identity, settings)` helper that both `require_mfa` and this route call). One producer (`providers.py`), one consumer helper. The checklist must enumerate this route as a known second MFA gate that has to move when the passkey factor lands.

---

## F7 — Middleware swallows verification errors and stashes identity; `_verify_request_identity` trusts the stash by raw-token match (MEDIUM — verify, not yet a hole)

**Path:** `DatabaseSessionMiddleware._resolve_schema_from_request` (`db/middleware.py:54-65`) calls `verify_token`, stashes `request.state.verified_identity`, and **swallows all errors** (`:72-73`). Downstream `_verify_request_identity` (`service.py:253-257`) returns the stashed identity if `stashed_token == token`. The stash carries `mfa_satisfied` from the verifier.

- This is *safe today* because the stash is only written *after* a successful `verify_token` (a swallowed error writes nothing), and the reuse is keyed to the exact raw token. But two things to lock for the passkey work: (1) the middleware pre-pass runs **only when `multi_tenancy_enabled`** — in single-tenant mode there is no stash and the dependency tree verifies itself, so the `mfa_satisfied` derivation must be identical on both paths (it is, both go through `FirebaseVerifier.verify_from_decoded`); (2) if the passkey factor is ever added as a *post-verification* enrichment (e.g. "look up whether this session did a passkey step-up") rather than baked into the token claim, the swallow-and-stash pre-pass could cache a stale/un-enriched `mfa_satisfied`.
- **Severity:** MEDIUM (latent; depends on F2's implementation choice).
- **Fix:** Keep the passkey factor *in the token claim* (F2), so the stashed `VerifiedIdentity.mfa_satisfied` is always authoritative and the raw-token key guarantees freshness. Add a checklist assertion: "no code path may upgrade `mfa_satisfied` after the verifier returns" — the boolean is set once, at the single producer, from token contents only.

---

## Summary table

| # | Bypass path | Cite | Skips passkey because | Severity |
|---|---|---|---|---|
| F1 | (removed, pablo#472) IAP mode early-returned from `require_mfa`; `verify_iap_jwt` had no callers | `service.py` (iap branch), `iap.py` (deleted) | returned before `mfa_satisfied` read; no in-app IAP verification existed | RESOLVED |
| F2 | Custom-token mint can't set `sign_in_second_factor`; verifier reads only that | `providers.py:123` | the single MFA producer reads a claim the passkey path can't populate | CRITICAL (design) |
| F3 | E2E bypass keyed on `email`+self-assertable `email_verified`, not a structured pattern | `service.py` (e2e branch) | returns before `mfa_satisfied` read | HIGH (non-prod PHI) |
| F4 | `is_development` blanket-skips MFA+idle+admin off one env var | `service.py`, `idle_session.py` | three early-returns, none read the boolean | MEDIUM |
| F5 | `get_current_user_id` is token-valid-only (no `require_mfa`/idle) | `service.py:292` | not in `require_mfa` chain | HIGH (latent) |
| F6 | Native `/native/code` hand-rolls the MFA check, won't see passkeys | `routes/auth.py:110-115` | parallel gate, doesn't use `mfa_satisfied`/verifier seam | HIGH |
| F7 | Middleware swallows errors + stashes `mfa_satisfied`; reuse by raw-token key | `middleware.py:54-73`, `service.py:253-257` | latent if passkey factor is post-verification enrichment | MEDIUM |

**The genuine backdoor to fix before/with the passkey build: F6 (second MFA gate that bypasses the seam). The two design constraints the checklist must hard-encode: F2 (the passkey factor must be a server-only token claim OR'd in at `providers.py:123`, the single producer) and F5/F7 (one producer, one consumer-helper; `mfa_satisfied` is set once from token contents and never upgraded later). F1 is already closed (pablo#472).**

Relevant files: `backend/app/auth/service.py`, `backend/app/auth/providers.py`, `backend/app/auth/idle_session.py`, `backend/app/db/middleware.py`, `backend/app/routes/auth.py`, `backend/app/auth/route_security.py`.

### Skeptic 3

I have the complete picture grounded in real code. The decisive fact for the passkey design: the locked decision is "Firebase = first-factor/session only; we verify the WebAuthn assertion then mint a Firebase custom token." A Firebase **custom token** does NOT carry a `firebase.sign_in_second_factor` claim — that claim is only set by Firebase's native MFA (TOTP) flow. This is the crux of multiple bypasses.

Here are my findings.

---

# ADVERSARIAL FINDINGS: paths to PHI that skip the passkey check

All cites `backend/app/...`. The locked design = "verify WebAuthn assertion server-side, then mint a **Firebase custom token**; Firebase is first-factor/session only." The MFA gate (`providers.py:123`) reads `firebase.sign_in_second_factor`. **A Firebase custom token (`createCustomToken` → client `signInWithCustomToken`) does NOT populate `firebase.sign_in_second_factor`** — that subfield is set only by Firebase's *native* multi-factor sign-in. This single fact drives findings 1, 2, and 5.

---

## Finding 1 — CRITICAL: passkey-minted custom token produces `mfa_satisfied=False`, so the design as written cannot pass its own gate — and the "fix" temptation is the backdoor

`providers.py:122-123`
```python
firebase_claims = decoded.get("firebase", {})
mfa_satisfied = bool(firebase_claims.get("sign_in_second_factor"))
```
A passkey assertion verified server-side and turned into a Firebase **custom token** carries no `sign_in_second_factor`. So a legitimately passkey-authenticated user hits `require_mfa` (`service.py:372`) and gets 403 `MFA_REQUIRED`. The dangerous "fix" a builder will reach for is to make `verify_from_decoded` treat the custom-token's presence (or a custom claim the *client* can influence) as MFA-satisfied — which then makes **every** custom token (including ones minted by any other Firebase Admin path) MFA-satisfied.

**Why it skips the passkey check:** the gate would be reading a Firebase-side signal that does not encode "a passkey was verified by *our* verifier." Nothing in the verified-identity contract ties `mfa_satisfied=True` back to a server-side WebAuthn assertion this request actually performed.

**Severity: Critical** (it is the central design seam; getting it wrong is a silent platform-wide MFA bypass).

**Exact fix:** Do NOT infer MFA from "is a custom token." Mint the custom token with a server-controlled **custom claim** that only the passkey-verification endpoint can set (e.g. `additional_claims={"pbk_aal": "passkey", "pbk_at": <unix>}` via `auth.create_custom_token(uid, {...})`), and gate it: add a `PasskeyVerifier` interpretation inside `FirebaseVerifier.verify_from_decoded` that sets `mfa_satisfied = firebase_claims.get("sign_in_second_factor") OR (claims.get("pbk_aal")=="passkey" and claims.get("pbk_at") is recent AND the (uid, credential_id) is present in the platform passkey table)`. The recency + DB-presence binding is what stops a stale/forged claim. Custom claims in Firebase are settable ONLY via Admin SDK, so the client cannot forge `pbk_aal` — but you must still verify the row exists so a revoked passkey can't ride an old token.

---

## Finding 2 — CRITICAL (transition period): the legacy TOTP path and the new passkey path are OR'd, but the disable-TOTP step is the gap

During transition, both `firebase.sign_in_second_factor` (legacy TOTP) AND the new passkey claim are accepted (the OR in Finding 1's fix). That is correct *until* a user enrolls a passkey and you intend passkeys to *replace* TOTP. If TOTP enrollment is left active in Firebase, an attacker who phishes the user's password + TOTP seed still gets in with `sign_in_second_factor=true` and never touches the (phishing-resistant) passkey. More subtly: a user who *thinks* they migrated to passkeys still has a live TOTP factor as a weaker parallel door.

**Why it skips the passkey check:** `require_mfa` is satisfied by the legacy branch of the OR; the passkey verifier is never invoked.

**Severity: Critical** during the window, degrading to High once migration completes — but only if migration actually unenrolls Firebase MFA factors, which nothing in the codebase does today.

**Exact fix:** Make the transition explicit and terminal per-user. When a user completes passkey enrollment, (a) record `passkey_only=true` on the platform user/identity row, and (b) in `verify_from_decoded`, once `passkey_only` is set for that subject, **stop honoring** `sign_in_second_factor` (require the passkey claim). Pair with an out-of-band job that calls `firebase_auth` to unenroll the user's TOTP second factor after a grace period. Don't leave "both doors open forever" as the steady state.

---

## Finding 3 — CRITICAL (OIDC path): a configured OIDC issuer is a parallel MFA gate that never knows about passkeys

`service.py:219-226` + `providers.py:213-222`
```python
def _oidc_mfa_satisfied(claims):
    amr = claims.get("amr")
    if isinstance(amr, list) and any(m in ("mfa", "otp") for m in amr):
        return True
    return claims.get("acr") == "mfa"
```
If `oidc_issuer` is set (the registry appends `OidcVerifier`, `service.py:219`), ANY token whose `iss`/`aud`/JWKS validate AND that carries `amr:["mfa"]` or `acr:"mfa"` satisfies `require_mfa` with **zero** passkey involvement. The passkey design lives entirely in the Firebase verifier. Whoever controls (or misconfigures) the OIDC realm controls `amr`/`acr`. A Keycloak realm where the admin set `acr=mfa` for a flow that is actually just password, or an issuer that mints `amr:["otp"]` for SMS, fully bypasses the phishing-resistant requirement.

**Why it skips the passkey check:** OIDC and passkey are independent verifiers; `mfa_satisfied` from OIDC is asserted by the external IdP, not by Pablo's WebAuthn verification.

**Severity: Critical** if OIDC is ever enabled in a PHI deployment alongside the passkey mandate; the two MFA definitions are not reconciled.

**Exact fix:** Decide the policy explicitly. If the passkey mandate is platform-wide, either (a) refuse to enable OIDC in deployments that require passkeys (validate in `Settings`: `oidc_issuer` and "passkey required" are mutually exclusive), or (b) tighten `_oidc_mfa_satisfied` to require a phishing-resistant AMR value (`amr` containing `"hwk"`/`"swk"`/`"phr"` per RFC 8176/8485, NOT plain `otp`/`mfa`) so SMS/TOTP-class AMR no longer counts as satisfying the passkey-grade gate.

---

## Finding 4 — HIGH: `get_current_user_id` reaches authenticated routes with NO `require_mfa` and NO idle gate — passkey never enters the chain

`service.py:292-328`. `get_current_user_id` calls `_verify_request_identity` (`:318`) directly and returns a resolved user id. It does **not** depend on `require_mfa`, `enforce_idle_session`, or `get_tenant_context`. It also arms RLS via `_resolve_user`'s shared seam only on the heavier paths — `get_current_user_id` sets `user_id_var` (`:327`) but does not run the MFA branch at all. Any route hanging off `Depends(get_current_user_id)` is **token-valid-only**: a bare Firebase session token (password only, or a non-MFA custom token) reaches it.

**Why it skips the passkey check:** the dependency simply never references `mfa_satisfied`. The route-posture test (`tests/test_route_mfa_guardrails.py`) classifies routes into four postures, but `get_current_user_id` is treated as authenticated — it is NOT in the MFA-required transitive set, yet it is not pre-MFA-enrollment either. It is an unlabeled fifth posture.

**Severity: High** — depends on which routes use it; any PHI-adjacent route on this dep is a clean MFA bypass.

**Exact fix:** Audit every `Depends(get_current_user_id)` usage. Either re-base it on `enforce_idle_session`/`require_mfa` (so it can't return without MFA), or formally enumerate it as a distinct, non-PHI posture in `route_security.py` and add a guardrail-test assertion that no route combining `get_current_user_id` touches a PHI surface. Today it is a "token-valid-only" door the passkey work will not cover unless explicitly closed.

---

## Finding 5 — HIGH (native desktop path): hand-rolled MFA gate bypasses the verifier seam entirely

`routes/auth.py:108-115` (`create_native_code`) reads `firebase_claims.get("sign_in_second_factor")` directly and raises `MFA_REQUIRED`. This is a **second, copy-pasted** MFA gate that never goes through `providers.py`/`mfa_satisfied`. When passkey support lands in `verify_from_decoded`, this path will NOT inherit it: a passkey-minted custom token (Finding 1) fails this gate (false negative), and conversely if you "fix" Finding 1 only in `providers.py`, the native path still won't recognize passkeys — so the desktop app either can't use passkeys or someone widens this gate independently and drifts. Also note `exchange_native_code` (`:124-159`) re-issues the stored `id_token`/`refresh_token` with **no** MFA re-check — the gate is only at code creation, so the refresh token handed to the native app re-authenticates indefinitely with whatever factor (or none) was minted.

**Why it skips the passkey check:** it predates and bypasses the verifier abstraction; the map flags it explicitly as "parallel hand-rolled MFA gate (does NOT use mfa_satisfied)."

**Severity: High** — drift surface; guarantees the native client is on a different MFA definition than the web client.

**Exact fix:** Replace the inline check with the shared seam: build a `VerifiedIdentity` (call `verify_token`/`FirebaseVerifier().verify_from_decoded(decoded_token)`) and gate on `identity.mfa_satisfied` so the native path inherits passkey support automatically. There must be exactly one place that interprets `mfa_satisfied`.

---

## Finding 6 — HIGH: middleware swallows verification errors and pre-stashes identity; a passkey verifier that raises non-401 (or the stash itself) can be ridden

`db/middleware.py:54-74`. `_resolve_schema_from_request` calls `verify_token` and **stashes the verified identity** on `request.state.verified_identity` (`:61-62`), then swallows all exceptions (`:72-73`). The downstream chain (`_verify_request_identity`, `service.py:253-257`) **trusts the stash if `verified_identity_token == token`** and returns it without re-verifying. Two risks for the passkey build: (a) if a future `PasskeyVerifier` does its DB/recency check at verify time and the middleware path skips part of it, the stash records `mfa_satisfied` computed in the pre-pass and the Depends tree never re-derives it; (b) the swallow means a passkey verifier raising anything other than 401 in the pre-pass is silently ignored — schema resolution just returns None and the request proceeds to the Depends tree which re-verifies, but any *state-dependent* passkey check (e.g. "is this credential still active?") that the verifier performs is recomputed inconsistently between the two passes.

**Why it skips the passkey check:** the `mfa_satisfied` value is computed once (possibly in the swallowed-error pre-pass) and cached; a revocation that happens between pre-pass and the gate is not re-checked.

**Severity: High** — turns `mfa_satisfied` into a per-request cached boolean rather than a live check; bad for passkey/credential revocation freshness.

**Exact fix:** Keep `mfa_satisfied` derivation free of any check that can change within a request, OR re-validate the passkey-credential's active state in `require_mfa` (not just at mint/verify time). Ensure the stash includes the credential id and `require_mfa` re-confirms the row is active+non-revoked against the DB even on a stash hit. Don't let "middleware already verified it" mean "skip the revocation check."

---

## Finding 7 — MEDIUM: dev / E2E bypasses sit above the passkey gate, and the prod-project pattern is the load-bearing guard

`require_mfa` (`service.py`): `is_development` returns the token before any factor check; the E2E emails bypass when `not is_prod_project`. These already gate on env, but the passkey rollout adds a new way to be wrong: if a deployment runs `require_mfa=False` or `is_development` true against real PHI (a misconfig), passkeys are entirely moot. The relevant transition risk: `is_prod_project` is pattern-matched on `gcp_project_id`; a new production project whose id doesn't match the prod pattern would be treated as non-prod and honor the E2E/MFA bypasses while serving real PHI.

**Why it skips the passkey check:** these branches `return decoded_token` before `mfa_satisfied` is consulted at all.

**Severity: Medium** (config-dependent, not a code logic hole), but load-bearing for the passkey mandate.

**Exact fix:** Add a startup assertion (`main.py`) that in any PHI-serving deployment, `require_mfa=True` and `is_development=False`. Verify the prod-project pattern matches every real production project id before launch; a non-matching prod project silently enables the E2E MFA bypass.

---

## Summary table

| # | Path | File:line | Skips passkey because | Severity |
|---|------|-----------|----------------------|----------|
| 1 | Custom-token mint has no `sign_in_second_factor` | `providers.py:122-123` | gate reads a Firebase signal passkey custom tokens don't set; naive fix trusts all custom tokens | Critical |
| 2 | Legacy TOTP OR passkey, TOTP never unenrolled | `providers.py:123` + Firebase MFA state | legacy branch satisfies gate; weaker parallel door stays open | Critical |
| 3 | OIDC `amr`/`acr` satisfies MFA independently | `service.py:219-226`, `providers.py:213-222` | OIDC verifier asserts MFA with no passkey; SMS/TOTP-class AMR counts | Critical |
| 4 | `get_current_user_id` has no MFA/idle dep | `service.py:292-328` | dependency never references `mfa_satisfied` | High |
| 5 | Native desktop hand-rolled gate | `routes/auth.py:108-115`, exchange `:124-159` | bypasses verifier seam; won't inherit passkey; refresh re-issue unchecked | High |
| 6 | Middleware swallow + identity stash | `db/middleware.py:54-74`, `service.py:253-257` | `mfa_satisfied` cached once; credential revocation not re-checked | High |
| 7 | dev/E2E early returns + prod-project pattern | `service.py` (dev/e2e branches) | branches return before `mfa_satisfied`; misconfig disables passkey wholesale | Medium |

**The single most important closure:** `mfa_satisfied=True` must mean "this exact request is backed by a server-verified WebAuthn assertion against an *active* credential row," enforced at ONE seam (`verify_from_decoded`) that the native path (#5) also routes through, with the legacy-TOTP and OIDC doors explicitly reconciled (#2, #3) rather than left as parallel definitions. Default disposition for #1–#3: these ARE backdoors unless the build closes them.
