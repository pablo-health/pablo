// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ServerSessionClaims } from "./types"

/** Where a session whose email is unverified is sent. */
export const VERIFY_EMAIL_PATH = "/verify-email"

/**
 * True when the sign-in provider says this session's email is unverified.
 *
 * The API will not tie an unverified email to a practice, so every practice
 * request from such a session fails. Only an explicit ``false`` counts: a
 * provider that does not report the claim is not treated as unverified.
 */
export function needsEmailVerification(claims: ServerSessionClaims): boolean {
  return Boolean(claims.email) && claims.emailVerified === false
}
