// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell's fetch layer: resolve a practice, redeem an
 * invitation, rotate a session.
 *
 * Deliberately does NOT use `get`/`post` from `@/lib/api/client`. Those fall
 * back to the signed-in clinician's Firebase ID token when no token is
 * passed, and nobody on this surface holds one. A patient's principal is a
 * portal session token minted by the engine's own signer, so this module
 * owns its `fetch` and its `Authorization` header. `buildApiUrl` is shared,
 * because where the backend lives is not this module's business. The same
 * reasoning the intake and messaging clients are written to.
 */

import { buildApiUrl } from "@/lib/api/client"

/** Mirrors the backend's practice resolution response. */
export interface PortalPracticeResolution {
  slug: string
  display_name: string
}

export type ResolvePracticeResult = { ok: true; data: PortalPracticeResolution } | { ok: false }

/**
 * Resolve a slug to its practice display name. 404 (unknown, or a portal
 * this deployment does not serve — the backend never says which) and any
 * network failure both collapse to `{ ok: false }`; the shell renders one
 * generic dead end either way.
 */
export async function resolvePortalPractice(slug: string): Promise<ResolvePracticeResult> {
  try {
    const response = await fetch(
      buildApiUrl(`/api/portal/practices/${encodeURIComponent(slug)}`),
      { method: "GET", headers: { Accept: "application/json" } },
    )
    if (!response.ok) return { ok: false }
    return { ok: true, data: (await response.json()) as PortalPracticeResolution }
  } catch {
    return { ok: false }
  }
}

/**
 * Mirrors the backend's `PatientSessionResponse`.
 *
 * The practice comes back with the credential because the page that redeems
 * an invitation may not know it yet: an invitation link carries its token in
 * the URL fragment, and the response is what names the practice the session
 * belongs to.
 */
export interface PortalSessionPayload {
  session_token: string
  token_type: string
  expires_at: number
  practice_slug: string
  practice_display_name: string
}

export type PortalAuthResult = { ok: true; data: PortalSessionPayload } | { ok: false }

async function postPortalAuth(
  path: string,
  body: Record<string, string>,
): Promise<PortalAuthResult> {
  try {
    const response = await fetch(buildApiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    })
    if (!response.ok) return { ok: false }
    return { ok: true, data: (await response.json()) as PortalSessionPayload }
  } catch {
    return { ok: false }
  }
}

/** Exchange a magic-link token plus its texted code for a patient session. */
export function redeemInvite(token: string, otp: string): Promise<PortalAuthResult> {
  return postPortalAuth("/api/patient/auth/redeem", { token, otp })
}

/** Rotate a live patient session into a fresh one — the bootstrap validity probe. */
export function refreshSession(sessionToken: string): Promise<PortalAuthResult> {
  return postPortalAuth("/api/patient/auth/refresh", { session_token: sessionToken })
}
