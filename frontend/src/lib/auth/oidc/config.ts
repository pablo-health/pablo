// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Auth.js v5 configuration for the OIDC auth provider (Keycloak).
 *
 * Tokens are short-lived; the `jwt` callback persists the id_token, refresh
 * token, and expiry on every sign-in and rotates them automatically when the
 * access window closes. The `session` callback surfaces the current id_token
 * so both client and server code can read it from `session.idToken`.
 *
 * Environment variables:
 *   AUTH_SECRET            — cookie encryption key (required by Auth.js)
 *   AUTH_KEYCLOAK_ID       — client id (e.g. "pablo-frontend")
 *   AUTH_KEYCLOAK_SECRET   — client secret
 *   AUTH_KEYCLOAK_ISSUER   — realm issuer URL
 *                            (e.g. http://localhost:8080/realms/pablo-dev)
 */

import NextAuth from "next-auth"
import Keycloak from "next-auth/providers/keycloak"
import type { JWT } from "next-auth/jwt"

/** How many seconds before nominal expiry we attempt a proactive refresh. */
const REFRESH_BUFFER_SECONDS = 60

/** Shape of the token endpoint response from Keycloak. */
interface KeycloakTokenResponse {
  access_token?: string
  refresh_token?: string
  id_token?: string
  expires_in?: number
  error?: string
  error_description?: string
}

/**
 * Exchange a refresh token for a fresh set of tokens from Keycloak's token
 * endpoint. Returns the updated JWT fields on success, or a token marked with
 * `error: "RefreshTokenError"` so the app can force a re-login.
 */
async function refreshKeycloakToken(token: JWT): Promise<JWT> {
  const issuer = process.env.AUTH_KEYCLOAK_ISSUER
  const clientId = process.env.AUTH_KEYCLOAK_ID
  const clientSecret = process.env.AUTH_KEYCLOAK_SECRET

  if (!issuer || !clientId || !clientSecret || !token.refreshToken) {
    return { ...token, error: "RefreshTokenError" }
  }

  const tokenEndpoint = `${issuer}/protocol/openid-connect/token`

  try {
    const response = await fetch(tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: token.refreshToken as string,
      }),
    })

    const refreshed = (await response.json()) as KeycloakTokenResponse

    if (!response.ok || refreshed.error) {
      console.error("Keycloak token refresh failed:", refreshed.error, refreshed.error_description)
      return { ...token, error: "RefreshTokenError" }
    }

    return {
      ...token,
      idToken: refreshed.id_token ?? token.idToken,
      refreshToken: refreshed.refresh_token ?? token.refreshToken,
      expiresAt: Math.floor(Date.now() / 1000) + (refreshed.expires_in ?? 300),
      error: undefined,
    }
  } catch (err) {
    console.error("Keycloak token refresh request threw:", err)
    return { ...token, error: "RefreshTokenError" }
  }
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Keycloak({
      clientId: process.env.AUTH_KEYCLOAK_ID,
      clientSecret: process.env.AUTH_KEYCLOAK_SECRET,
      issuer: process.env.AUTH_KEYCLOAK_ISSUER,
    }),
  ],

  callbacks: {
    /**
     * Persist the id_token and refresh credentials in the encrypted JWT
     * cookie. On every call after the initial sign-in we check expiry and
     * rotate the tokens if the window has passed.
     */
    async jwt({ token, account }) {
      // Initial sign-in: stash the tokens from the IdP response.
      if (account) {
        return {
          ...token,
          idToken: account.id_token,
          refreshToken: account.refresh_token,
          expiresAt: account.expires_at,
          error: undefined,
        }
      }

      // Token still valid — return as-is.
      const expiresAt = (token.expiresAt as number | undefined) ?? 0
      if (Date.now() / 1000 < expiresAt - REFRESH_BUFFER_SECONDS) {
        return token
      }

      // Token expired (or within the buffer) — refresh.
      return refreshKeycloakToken(token)
    },

    /**
     * Expose the id_token and any error flag on the session object so both
     * client components (via `useSession`) and server code (via `auth()`)
     * can read `session.idToken`.
     */
    async session({ session, token }) {
      return {
        ...session,
        idToken: (token.idToken as string | undefined) ?? null,
        error: (token.error as string | undefined) ?? null,
      }
    },
  },

  // Use JWT strategy (stateless, no database needed).
  session: { strategy: "jwt" },

  // The Auth.js /api/auth/* routes are handled by the [...nextauth] route.
  // We do not need a custom `pages` config — Keycloak hosts its own login UI.
})
