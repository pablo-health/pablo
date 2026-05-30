// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * OIDC implementation of the core client-side {@link ClientAuthProvider}
 * contract: live auth state, id-token resolution, and full sign-out
 * (including RP-initiated logout so the IdP session is cleared).
 *
 * This module IS the `oidc` provider — the rest of the app reaches it
 * only through `@/lib/auth/provider`, never directly. Auth.js v5
 * (next-auth@beta) manages the session cookie and token rotation.
 */

import { useSession, signOut as nextAuthSignOut, getSession } from "next-auth/react"
import type { AuthState, AuthUser, ClientAuthProvider } from "@/lib/auth/types"

/** Extend the Auth.js session type with the fields we add in config.ts. */
interface OidcSession {
  idToken: string | null
  error: string | null
  /** OIDC subject id, surfaced by the `session` callback in config.ts. */
  sub?: string | null
  user?: {
    name?: string | null
    email?: string | null
    image?: string | null
  }
}

function toAuthUser(session: OidcSession): AuthUser | null {
  if (!session.user && !session.idToken) return null

  // uid comes from the OIDC subject id surfaced server-side by the session
  // callback — no client-side token parsing (id_token payloads are
  // base64url, which `atob` mis-decodes).
  return {
    uid: session.sub ?? "",
    email: session.user?.email ?? null,
    displayName: session.user?.name ?? null,
    photoURL: session.user?.image ?? null,
  }
}

/**
 * React hook returning live auth state for the OIDC provider.
 * Delegates to Auth.js `useSession()` which is supplied by the
 * `<SessionProvider>` mounted in `oidc/SessionProviderWrapper.tsx`.
 */
export function useOidcAuthState(): AuthState {
  const { data: session, status } = useSession()

  if (status === "loading") {
    return { user: null, loading: true }
  }

  if (!session) {
    return { user: null, loading: false }
  }

  const oidcSession = session as unknown as OidcSession
  const user = toAuthUser(oidcSession)
  return { user, loading: false }
}

/**
 * Return the current id_token for `Authorization` header use.
 *
 * For `forceRefresh=true` the caller (retry-on-401 path) wants a
 * genuinely fresh token. We hit the Auth.js session endpoint directly with
 * a cache-bypass header — this causes Auth.js to re-evaluate the `jwt`
 * callback on the server, which runs the Keycloak refresh-token exchange
 * if the token is expired, and returns the updated session including the
 * fresh id_token.
 *
 * Returns null when signed out or when the token is unavailable.
 */
export async function getOidcIdToken(forceRefresh = false): Promise<string | null> {
  if (typeof window === "undefined") return null

  if (forceRefresh) {
    // Bypass any in-memory session cache by fetching the session endpoint
    // directly. Auth.js re-runs the jwt callback server-side, which invokes
    // the Keycloak refresh-token grant when the token is past its expiry.
    try {
      const res = await fetch("/api/auth/session", {
        headers: { "Cache-Control": "no-cache" },
      })
      if (res.ok) {
        const session = (await res.json()) as OidcSession | null
        return session?.idToken ?? null
      }
    } catch {
      // Fetch failed — fall through to getSession() as a safety net.
    }
  }

  const session = await getSession()
  const oidcSession = session as unknown as OidcSession | null
  return oidcSession?.idToken ?? null
}

/**
 * Full sign-out: clears the Auth.js session cookie AND performs
 * RP-initiated logout to clear the Keycloak IdP session. Without the
 * RP-initiated step, returning to `/login` would silently re-authenticate
 * the same user through the existing Keycloak session cookie.
 *
 * Best-effort: if the IdP `end_session_endpoint` is unavailable (e.g. dev
 * Keycloak down) we still complete local sign-out and let the caller
 * redirect.
 */
export async function oidcSignOut(): Promise<void> {
  // 1. Capture the id_token BEFORE clearing the local session. Keycloak's
  //    end-session endpoint needs it as `id_token_hint` for a clean,
  //    prompt-free RP-initiated logout that honors post_logout_redirect_uri
  //    (without the hint, Keycloak ≥18 shows a confirmation interstitial and
  //    may ignore the redirect). Once we sign out locally the token is gone,
  //    so the ordering here is load-bearing.
  let idToken: string | null = null
  try {
    const session = (await getSession()) as unknown as OidcSession | null
    idToken = session?.idToken ?? null
  } catch {
    // No session to read — proceed; logout falls back to a local redirect.
  }

  // 2. Resolve Keycloak's end_session_endpoint and build the logout URL
  //    (best-effort). Discovery needs the public issuer URL.
  let endSessionUrl: string | null = null
  const issuer = process.env.NEXT_PUBLIC_KEYCLOAK_ISSUER
  if (issuer) {
    try {
      const metaRes = await fetch(`${issuer}/.well-known/openid-configuration`)
      if (metaRes.ok) {
        const meta = (await metaRes.json()) as { end_session_endpoint?: string }
        if (meta.end_session_endpoint) {
          const url = new URL(meta.end_session_endpoint)
          url.searchParams.set(
            "post_logout_redirect_uri",
            `${window.location.origin}/login`
          )
          if (idToken) url.searchParams.set("id_token_hint", idToken)
          endSessionUrl = url.toString()
        }
      }
    } catch {
      // Discovery failed — fall through to a local redirect below.
    }
  }

  // 3. Clear the local Auth.js session cookie.
  try {
    await nextAuthSignOut({ redirect: false })
  } catch {
    // Cookie clear failed — still navigate below.
  }

  // 4. Prefer RP-initiated logout so the Keycloak SSO session is terminated;
  //    otherwise fall back to a local redirect. NOTE: the fallback leaves the
  //    IdP SSO cookie intact, so the next login can be silent — keep
  //    NEXT_PUBLIC_KEYCLOAK_ISSUER set in any OIDC deployment.
  window.location.assign(endSessionUrl ?? "/login")
}

export const oidcClientProvider: ClientAuthProvider = {
  id: "oidc",
  useAuthState: useOidcAuthState,
  getIdToken: getOidcIdToken,
  signOut: oidcSignOut,
}
