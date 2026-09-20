// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-session persistence for the portal shell.
 *
 * Bearer token only — no cookies, no Firebase. Persisted in `localStorage`,
 * namespaced per practice slug: the direct consequence of a sliding session
 * lifetime plus a revocation kill switch, so a patient's access survives a
 * closed tab the way a month-long session implies. Namespacing by slug is
 * not a security boundary — tenancy rides in the token's own claims, so
 * cross-slug confusion server-side is impossible regardless — it just keeps
 * two practices' tokens from clobbering each other in one browser.
 */

import { redeemInvite, refreshSession } from "./api"

export interface StoredPortalSession {
  sessionToken: string
  expiresAt: number
}

function storageKey(slug: string): string {
  return `pablo-portal-session:${slug}`
}

export function getStoredSession(slug: string): StoredPortalSession | null {
  if (typeof window === "undefined") return null
  const raw = window.localStorage.getItem(storageKey(slug))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<StoredPortalSession>
    if (typeof parsed.sessionToken !== "string" || typeof parsed.expiresAt !== "number") {
      return null
    }
    return { sessionToken: parsed.sessionToken, expiresAt: parsed.expiresAt }
  } catch {
    return null
  }
}

export function storeSession(slug: string, session: StoredPortalSession): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(storageKey(slug), JSON.stringify(session))
}

export function clearSession(slug: string): void {
  if (typeof window === "undefined") return
  window.localStorage.removeItem(storageKey(slug))
}

export type BootstrapResult =
  | { status: "active"; sessionToken: string }
  | { status: "none" }
  | { status: "expired" }

/**
 * The shell's load-time validity probe. There is no `/me` endpoint to ask,
 * and nothing patient-identifying renders until a slot mounts, so rotating
 * the stored token via `/refresh` IS the probe: a live token comes back
 * rotated, a dead one comes back a 401, and either way the caller learns
 * which without a second round trip.
 */
export async function bootstrapSession(slug: string): Promise<BootstrapResult> {
  const stored = getStoredSession(slug)
  if (!stored) return { status: "none" }

  const result = await refreshSession(stored.sessionToken)
  if (!result.ok) {
    clearSession(slug)
    return { status: "expired" }
  }
  storeSession(slug, {
    sessionToken: result.data.session_token,
    expiresAt: result.data.expires_at,
  })
  return { status: "active", sessionToken: result.data.session_token }
}

export interface RedeemedSession {
  sessionToken: string
  practiceSlug: string
  practiceDisplayName: string
}

export type RedeemAndStoreResult = { ok: true; session: RedeemedSession } | { ok: false }

/**
 * Redeem an invitation and, on success, persist the minted session.
 *
 * The practice comes from the RESPONSE, not from the caller. An invitation
 * link need not name the practice in its path, so the redemption is what
 * settles which practice this session belongs to — and therefore which key
 * it is stored under.
 */
export async function redeemAndStore(
  token: string,
  otp: string,
): Promise<RedeemAndStoreResult> {
  const result = await redeemInvite(token, otp)
  if (!result.ok) return { ok: false }
  const { session_token, expires_at, practice_slug, practice_display_name } = result.data
  storeSession(practice_slug, { sessionToken: session_token, expiresAt: expires_at })
  return {
    ok: true,
    session: {
      sessionToken: session_token,
      practiceSlug: practice_slug,
      practiceDisplayName: practice_display_name,
    },
  }
}
