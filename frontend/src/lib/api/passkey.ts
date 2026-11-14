// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Passkey (WebAuthn) API.
 *
 * Thin typed wrappers over the backend ceremony + management endpoints
 * (`backend/app/routes/passkey.py`). The begin endpoints return raw
 * WebAuthn options that feed straight into `@simplewebauthn/browser`; the
 * finish endpoints take the authenticator's response back.
 *
 * Authenticate begin/finish are deliberately unauthenticated — they ARE
 * the factor being asserted, so they run before any session exists. On the
 * sign-in path they send no token and don't wait for one (see `auth` on
 * `apiClient`): waiting for a token waits for the stored session to be
 * restored, which another tab of the app can hold up indefinitely. A step-up
 * still sends its token on finish, because the API uses it to bind the
 * assertion to the account already signed in.
 */

import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from "@simplewebauthn/browser"

import { apiClient, del, get, post } from "./client"

export interface PasskeyCredentialSummary {
  credential_id: string
  device_label: string | null
  transports: string[] | null
  backup_eligible: boolean
  created_at: string
  last_used_at: string | null
}

export interface PasskeyRegistrationResult {
  credential_id: string
  created_at: string
  // A Firebase custom token carrying the webauthn second-factor claim, minted
  // from the just-verified attestation. Exchange it via `signInWithCustomToken`
  // and force-refresh the ID token so a freshly-enrolled session clears MFA in
  // one ceremony — no sign-out/in before reaching protected data (PABLO-mee).
  custom_token?: string | null
  // One-time recovery codes, present only on the user's first second-factor
  // enrolment — show them once, then they're gone (the server keeps only hashes).
  backup_codes?: string[] | null
}

interface PasskeyAuthenticationResult {
  custom_token: string
}

export function beginRegistration(): Promise<PublicKeyCredentialCreationOptionsJSON> {
  return post<PublicKeyCredentialCreationOptionsJSON>("/api/auth/passkey/register/begin", {})
}

export function finishRegistration(
  credential: RegistrationResponseJSON,
  deviceLabel: string | null,
): Promise<PasskeyRegistrationResult> {
  return post<PasskeyRegistrationResult>("/api/auth/passkey/register/finish", {
    credential,
    device_label: deviceLabel,
  })
}

/**
 * Which ceremony an assertion belongs to. A "sign-in" has no session to
 * speak of; a "step-up" runs on the first-factor session it upgrades.
 */
export type PasskeyAssertionFlow = "sign-in" | "step-up"

/**
 * Fetch the options for an assertion. The API reads no session here in
 * either flow, so no token is sent.
 */
export function beginAuthentication(): Promise<PublicKeyCredentialRequestOptionsJSON> {
  return apiClient<PublicKeyCredentialRequestOptionsJSON>("/api/auth/passkey/authenticate/begin", {
    method: "POST",
    body: JSON.stringify({}),
    auth: "none",
  })
}

/**
 * Verify the assertion. A step-up sends the session's token, which the API
 * uses to refuse a passkey that belongs to a different account; a sign-in
 * sends none.
 */
export function finishAuthentication(
  credential: AuthenticationResponseJSON,
  flow: PasskeyAssertionFlow,
): Promise<PasskeyAuthenticationResult> {
  const body = { credential }
  if (flow === "step-up") {
    return post<PasskeyAuthenticationResult>("/api/auth/passkey/authenticate/finish", body)
  }
  return apiClient<PasskeyAuthenticationResult>("/api/auth/passkey/authenticate/finish", {
    method: "POST",
    body: JSON.stringify(body),
    auth: "none",
  })
}

/**
 * Spend a one-time recovery code as the SECOND factor.
 *
 * Runs on a first-factor session (the caller has just signed in with a
 * password), so `post` attaches that bearer token. The backend verifies and
 * consumes the single-use code and returns a custom token carrying the
 * `recovery` factor claim — exchange it via `signInWithCustomToken` and
 * force-refresh the ID token to reach an MFA-satisfied session.
 */
export function redeemRecoveryCode(code: string): Promise<PasskeyAuthenticationResult> {
  return post<PasskeyAuthenticationResult>("/api/auth/passkey/recovery-code/redeem", { code })
}

export function listPasskeys(): Promise<PasskeyCredentialSummary[]> {
  return get<PasskeyCredentialSummary[]>("/api/auth/passkey/credentials")
}

export function revokePasskey(credentialId: string): Promise<void> {
  return del<void>(`/api/auth/passkey/credentials/${encodeURIComponent(credentialId)}`)
}
