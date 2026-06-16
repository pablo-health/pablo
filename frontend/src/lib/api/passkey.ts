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
 * the factor being asserted, so they run before any session exists.
 */

import type {
  AuthenticationResponseJSON,
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
  RegistrationResponseJSON,
} from "@simplewebauthn/types"

import { del, get, post } from "./client"

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

export function beginAuthentication(): Promise<PublicKeyCredentialRequestOptionsJSON> {
  return post<PublicKeyCredentialRequestOptionsJSON>("/api/auth/passkey/authenticate/begin", {})
}

export function finishAuthentication(
  credential: AuthenticationResponseJSON,
): Promise<PasskeyAuthenticationResult> {
  return post<PasskeyAuthenticationResult>("/api/auth/passkey/authenticate/finish", {
    credential,
  })
}

export function listPasskeys(): Promise<PasskeyCredentialSummary[]> {
  return get<PasskeyCredentialSummary[]>("/api/auth/passkey/credentials")
}

export function revokePasskey(credentialId: string): Promise<void> {
  return del<void>(`/api/auth/passkey/credentials/${encodeURIComponent(credentialId)}`)
}
