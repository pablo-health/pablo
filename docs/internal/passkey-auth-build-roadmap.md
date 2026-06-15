# Passkey Auth — Build Roadmap (PABLO-egm)

> Phase tracker for the WebAuthn passkey migration. Pairs with the
> implementation spec (`docs/internal/passkey-auth-build-spec.md`) and the
> reviewer checklist (`docs/security/webauthn-security-review-checklist.md`).

## Locked decisions

- **Stay on Firebase** as the first-factor / session layer. Verify the
  WebAuthn assertion ourselves and mint a Firebase custom token carrying a
  server-only `pablo_amr: ["webauthn"]` factor claim. No separate identity
  server.
- **Libraries:** `webauthn` (`py_webauthn`) on the backend,
  `@simplewebauthn/browser` on the frontend.
- **Attestation: `none`.** We verify every assertion cryptographically but do
  not verify authenticator-model provenance (no FIDO Metadata Service / model
  allowlist). This is required for synced platform passkeys (iCloud Keychain,
  Google Password Manager) and is the standard choice when the policy is not
  "only these specific hardware tokens." Revisit only if a compliance
  requirement mandates attested provenance.
- **Enrolment gate.** The product is retiring TOTP, so we do **not** require
  an existing TOTP factor to enrol the first passkey. Instead: the first
  passkey may be enrolled from a first-factor session (enrol grants no PHI
  access); adding a subsequent passkey requires an already-MFA-satisfied
  session, so a phished password cannot bolt a rogue passkey onto a protected
  account.

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| **.1** | Credential + challenge tables in the platform schema | ✅ shipped (pablo#470) |
| — | Remove the unused IAP auth mode (pre-req cleanup) | ✅ shipped (pablo#472) |
| **.2 (PABLO-egm.1)** | Backend endpoints + enforcement integration | ✅ this change |
| **.3 (PABLO-egm.2)** | Frontend enrol / login / manage UI | 📋 next |
| **.4 (PABLO-egm.3)** | Backup codes + account recovery | 📋 |
| **.5 (PABLO-egm.4)** | Cutover — retire TOTP, per-user `passkey_only` | 📋 |
| **.6 (PABLO-egm.5)** | Independent security review | 📋 |

## What .2 (this change) delivers

- `webauthn` dependency pinned (`pyproject.toml` + lockfile).
- Settings: `WEBAUTHN_RP_ID`, `WEBAUTHN_RP_NAME`, `WEBAUTHN_ORIGINS`.
- Router `routes/passkey.py` (registered in `main.py`):
  `POST /api/auth/passkey/{register,authenticate}/{begin,finish}`.
- `services/passkey_service.py` — ceremony orchestration, clone detection,
  custom-token mint.
- `services/passkey_challenge_store.py` — single-use SHA-256 challenge store
  (Postgres / Redis / in-memory), modeled on `launch_intent_store`.
- `repositories/passkey_credential.py` (+ postgres impl) — credential CRUD.
- Enforcement seam: `auth/providers.py::passkey_factor_satisfied` OR'd into
  `mfa_satisfied`, and the parallel native-code gate in `routes/auth.py`.
- Routes classified non-PHI in `check_route_audit.py`.
- Tests: enforcement seam, challenge single-use, enrolment gate, clone
  detection, mint.

No schema change in this slice — the tables shipped in .1, so there is no new
migration and no tenant-template regeneration.

## Carry-forward (tracked in the checklist)

- **H8** — confirm empirically whether `pablo_amr` survives a Firebase ID-token
  refresh; define a re-assertion / max-session policy if it does.
- **H10** — wire alerting on a `backup_eligible=false → true` BE flip.
- **Recovery (egm.3)** — design the lost-authenticator path so it does not
  reintroduce an H2 first-factor-only bypass as a "support workflow."
- **Per-environment config** — assert `require_mfa=True` / `is_development=False`
  on any PHI-serving deployment before cutover.
