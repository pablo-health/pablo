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

import { useSession, signOut as nextAuthSignOut } from "next-auth/react"
import type { AuthState, AuthUser, ClientAuthProvider } from "@/lib/auth/types"

/** Extend the Auth.js session type with the fields we add in config.ts. */
interface OidcSession {
  idToken: string | null
  error: string | null
  user?: {
    name?: string | null
    email?: string | null
    image?: string | null
  }
}

function toAuthUser(session: OidcSession): AuthUser | null {
  if (!session.user && !session.idToken) return null

  // Derive a stable uid from the id_token subject claim. Auth.js puts the
  // sub claim in session.user.name in some configurations, but we can also
  // parse it from the JWT payload directly as a fallback.
  let uid = ""
  if (session.idToken) {
    try {
      const payload = JSON.parse(atob(session.idToken.split(".")[1]))
      uid = (payload.sub as string) || ""
    } catch {
      // Malformed token — uid stays empty; won't affect auth behaviour.
    }
  }

  return {
    uid,
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

  const { getSession } = await import("next-auth/react")

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
  try {
    // Auth.js signOut clears the session cookie. `redirect: false` lets us
    // handle the navigation ourselves after the IdP logout completes.
    await nextAuthSignOut({ redirect: false })
  } catch {
    // Cookie clear failed — still attempt IdP logout below.
  }

  // RP-initiated logout: redirect through Keycloak's end_session_endpoint
  // so the IdP session (SSO cookie) is terminated. We discover the endpoint
  // from the OIDC issuer's well-known config. Best-effort — if discovery
  // fails we just navigate to /login.
  try {
    const issuer = process.env.NEXT_PUBLIC_KEYCLOAK_ISSUER
    if (issuer) {
      const metaRes = await fetch(`${issuer}/.well-known/openid-configuration`)
      if (metaRes.ok) {
        const meta = (await metaRes.json()) as { end_session_endpoint?: string }
        if (meta.end_session_endpoint) {
          const logoutUrl = new URL(meta.end_session_endpoint)
          logoutUrl.searchParams.set(
            "post_logout_redirect_uri",
            `${window.location.origin}/login`
          )
          window.location.assign(logoutUrl.toString())
          return
        }
      }
    }
  } catch {
    // Discovery failed — fall through to local redirect.
  }

  // Fallback: navigate to login without IdP logout. The local session is
  // already cleared so the user is signed out from this app.
  window.location.assign("/login")
}

export const oidcClientProvider: ClientAuthProvider = {
  id: "oidc",
  useAuthState: useOidcAuthState,
  getIdToken: getOidcIdToken,
  signOut: oidcSignOut,
}
