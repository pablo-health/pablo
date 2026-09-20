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
  /**
   * The deployment's CAPTCHA site key, or `null` when no provider is
   * configured. Public by definition — it goes into the widget — and it is
   * the same value for every slug, so it says nothing about the practice.
   */
  captcha_site_key?: string | null
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

/**
 * A POST or GET made AS the patient, with the session token as the bearer.
 *
 * The three calls below are the first on this surface that authenticate —
 * resolve, redeem and refresh all run before or across a session — so this
 * is where the `Authorization` header lives.
 */
async function callAsPatient(
  path: string,
  sessionToken: string,
  init: RequestInit = {},
): Promise<Response | null> {
  try {
    return await fetch(buildApiUrl(path), {
      ...init,
      headers: {
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        Accept: "application/json",
        Authorization: `Bearer ${sessionToken}`,
      },
    })
  } catch {
    return null
  }
}

/** Mirrors the backend's `SignOutResponse`. */
export interface SignOutPayload {
  sessions_revoked: number
}

export type SignOutResult = { ok: true; data: SignOutPayload } | { ok: false }

/**
 * Sign out — this device only, or everywhere.
 *
 * `everywhere` needs a stepped-up session and answers 403 without one; the
 * caller renders that as a message rather than as a failure, because the
 * request was understood and refused.
 */
export async function signOut(
  sessionToken: string,
  { everywhere = false }: { everywhere?: boolean } = {},
): Promise<SignOutResult> {
  const path = everywhere ? "/api/patient/auth/logout-all" : "/api/patient/auth/logout"
  const response = await callAsPatient(path, sessionToken, { method: "POST" })
  if (!response || !response.ok) return { ok: false }
  return { ok: true, data: (await response.json()) as SignOutPayload }
}

/**
 * Mirrors the backend's `PortalCapabilitiesResponse`.
 *
 * `modules` carries EVERY module the engine knows and a boolean, not a list
 * of the enabled ones, so a client can tell "off" from "I have never heard
 * of it" — and so a module added later arrives as a key rather than as
 * silence.
 */
export interface PortalCapabilities {
  practice: { display_name: string | null }
  modules: Record<string, boolean>
  auth_strength: string
}

export type CapabilitiesResult = { ok: true; data: PortalCapabilities } | { ok: false }

/**
 * What this portal serves, for the shell to render its navigation from.
 *
 * A failure is `{ ok: false }` and the shell renders no navigation rather
 * than guessing at one. Guessing would mean showing a tab that leads to a
 * 404, which is worse than showing nothing: every module route is mounted
 * only when the deployment names it, so the shell hiding something is a
 * courtesy and never the control.
 */
export async function fetchCapabilities(sessionToken: string): Promise<CapabilitiesResult> {
  const response = await callAsPatient("/api/patient/capabilities", sessionToken)
  if (!response || !response.ok) return { ok: false }
  return { ok: true, data: (await response.json()) as PortalCapabilities }
}

/**
 * Ask a practice to send a fresh sign-in link to an email address.
 *
 * ALWAYS resolves the same way when the request reached the server, because
 * the server always answers 202 — whether or not the address belongs to
 * anybody. The page shows one message either way, and must not be tempted
 * to say more: whether somebody is a patient of a therapy practice is not a
 * fact this page gets to confirm.
 *
 * `{ ok: false }` therefore means the request did not get through — a
 * network failure, a closed rate-limit window, a CAPTCHA refusal — and
 * never "no such patient".
 */
export async function requestPortalRecovery(
  slug: string,
  email: string,
  captchaToken?: string | null,
): Promise<{ ok: boolean }> {
  try {
    const response = await fetch(
      buildApiUrl(`/api/portal/practices/${encodeURIComponent(slug)}/recover`),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          ...(captchaToken ? { "X-Captcha-Token": captchaToken } : {}),
        },
        body: JSON.stringify({ email }),
      },
    )
    return { ok: response.ok }
  } catch {
    return { ok: false }
  }
}
