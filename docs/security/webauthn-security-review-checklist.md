# WebAuthn Passkey — Security-Review Checklist

> Reviewer checklist for the passkey authentication backend (epic
> **PABLO-egm**). Derived from the enforcement-hardening list in
> `docs/internal/passkey-auth-build-spec.md` and intended as the
> standing input to the independent security review (**PABLO-egm.5**).
> Each item names the bypass it closes and where it is enforced in code.

## The one invariant everything rests on

`mfa_satisfied == True` must mean **"this exact request is backed by a
server-verified WebAuthn assertion against an active credential, OR a
Firebase-native second factor."** A Firebase **custom token** (how a
passkey session is established) cannot carry the reserved
`firebase.sign_in_second_factor` claim, so the passkey factor rides a
server-minted `pablo_amr: ["webauthn"]` claim that is stamped **only**
by the assertion-finish endpoint, only after a verified assertion, in
the same request.

Single producer: `services/passkey_service.py::PasskeyService.finish_authentication`.
Single reader: `auth/providers.py::passkey_factor_satisfied`, OR'd into
`mfa_satisfied` at `FirebaseVerifier.verify_from_decoded` and re-used by
the native-code gate in `routes/auth.py`.

## CRITICAL — ship-blockers

- [x] **H1 — `pablo_amr` is stamped only post-assertion, never client-supplied.**
  Minted only in `finish_authentication` after `verify_authentication_response`
  succeeds; never derived from a first-factor token, never copied across
  another mint. There is exactly one `create_custom_token` call site.
- [x] **H2 — password-reset / first-factor-only never reaches a passkey-satisfied token.**
  The mint requires a fresh verified assertion against a stored credential;
  a first-factor session can only reach `register/*` (enrol), which grants
  **no** PHI access. Enrolling a passkey ≠ asserting one.
- [x] **H3 — the native-code MFA gate honours the passkey factor.**
  `routes/auth.py::create_native_code` ORs `passkey_factor_satisfied` into
  its inline check, so a passkey desktop login is neither wrongly rejected
  nor silently downgraded. Covered by `tests/test_passkey_enforcement.py`.
- [x] **H4 — enrolment is gated.** The first passkey may be enrolled from a
  first-factor session; a **second** one requires an already-MFA-satisfied
  session (`PasskeyService.begin_registration` step-up). Decision recorded
  in the roadmap (TOTP not required — the product is retiring TOTP).

## HIGH

- [x] **H5 — single-use, bound, short-TTL challenge.** Only `SHA-256(challenge)`
  is stored; consumed before any token is issued; `expires_at` (≤300s)
  re-checked server-side; register challenges bound to `user_id`; ceremony
  type matched on consume. `services/passkey_challenge_store.py`.
- [x] **H6 — origin + RP id + user-verification checked every ceremony.**
  `expected_origin=settings.webauthn_origins`, `expected_rp_id=settings.webauthn_rp_id`,
  `require_user_verification=True` on both register and authenticate verify.
- [x] **H7 — sign-count clone detection.** `new_sign_count <= stored` is
  rejected and logged, except the legitimate 0/0 platform case; the counter
  is advanced only on success. Covered by `tests/test_passkey_service.py`.
- [x] **H8 — custom-token blast radius.** `create_custom_token` exists in
  exactly one place (the mint). No general "mint a token for uid" helper.
  **Open:** confirm `pablo_amr` does not survive a Firebase ID-token refresh
  without re-assertion (build-spec OPEN QUESTION 2 — empirical check).

## MEDIUM

- [x] **H9 — rate-limit + log the public surface.** Both `authenticate/*`
  endpoints depend on `require_rate_limit`. Enrol/assert outcomes (success,
  clone-reject) are logged as security events, identifiers only, no PHI.
- [ ] **H10 — BE/BS transition monitoring.** `backup_eligible`/`backup_state`
  are captured at registration and `backup_state` re-evaluated on assertion.
  Alerting on a `backup_eligible=false → true` flip is **not yet** wired —
  follow-up.
- [x] **H11 — `excludeCredentials` on register / `allowCredentials` UX.**
  `excludeCredentials` blocks double-enrolment; transports are stored and fed
  back for UX but never trusted as a security control.
- [x] **H12 — revocation honoured on the hot path.** The assertion lookup
  (`get_active`) filters `revoked_at IS NULL`; soft-revoke preserves the
  audit trail.

## Inherited-skip cautions (config, not code holes)

- [ ] **Dev / E2E bypasses sit above the gate.** `require_mfa=False` and
  `is_development` short-circuit before `mfa_satisfied`. The passkey mandate
  assumes any PHI-serving deployment runs `require_mfa=True` and
  `is_development=False`. Verify per environment (build-spec F4/F7).
- [ ] **OIDC `amr`/`acr` is a parallel MFA definition.** If an OIDC issuer is
  ever enabled alongside the passkey mandate, reconcile `_oidc_mfa_satisfied`
  with the phishing-resistant requirement (build-spec Finding 3).
