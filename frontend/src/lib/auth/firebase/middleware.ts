// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Firebase implementation of route-protection middleware, built on
 * ``next-firebase-auth-edge``. The shared `middleware.ts` delegates here
 * through `@/lib/auth/middleware` based on the active provider.
 */

import { type NextRequest, NextResponse } from "next/server"
import { authMiddleware, redirectToLogin, redirectToHome } from "next-firebase-auth-edge"
import { authConfig, loginPath, logoutPath } from "@/lib/auth-config"
import { isForcedLogoutArrival } from "@/lib/auth/forced-logout"
import { extraPublicPaths } from "@/lib/auth/public-paths"
import { assertHttpsOrigin, generateNonce, NONCE_HEADER, requestHeadersWithNonce, STRIPE_JS } from "@/lib/auth/csp"
import { IS_DEV_MODE } from "@/lib/devMode"

const PUBLIC_PATHS = ["/login", "/native-auth", "/baa-acceptance", "/mfa-enrollment", "/api/config", "/api/auth/native", "/api/auth/exchange-setup-token", ...extraPublicPaths()]

const API_ORIGIN = assertHttpsOrigin("API_URL", process.env.API_URL || "")

function buildCsp(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' https://apis.google.com ${STRIPE_JS}`,
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self' data:",
    "img-src 'self' https: data:",
    `connect-src 'self' https://*.googleapis.com https://*.firebaseio.com https://*.cloudfunctions.net https://*.pablo.health ${API_ORIGIN} wss://*.firebaseio.com`.replace(/\s+/g, " ").trim(),
    `frame-src 'self' https://*.firebaseapp.com https://accounts.google.com ${STRIPE_JS}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://accounts.google.com",
  ].join("; ")
}

function addSecurityHeaders(response: NextResponse, nonce: string): NextResponse {
  response.headers.set("Content-Security-Policy", buildCsp(nonce))
  response.headers.set(
    "Strict-Transport-Security",
    "max-age=31536000; includeSubDomains; preload"
  )
  response.headers.set("X-Content-Type-Options", "nosniff")
  response.headers.set("X-Frame-Options", "DENY")
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin")
  response.headers.set(
    "Permissions-Policy",
    "geolocation=(), microphone=(), camera=()"
  )
  return response
}

export default async function firebaseAuthMiddleware(request: NextRequest) {
  const nonce = generateNonce()

  // Dev mode: skip auth, just add security headers
  if (IS_DEV_MODE) {
    const headers = requestHeadersWithNonce(request, buildCsp(nonce), nonce)
    return addSecurityHeaders(NextResponse.next({ request: { headers } }), nonce)
  }

  return authMiddleware(request, {
    loginPath,
    logoutPath,
    ...authConfig,

    handleValidToken: async (_tokens, headers) => {
      const { pathname, searchParams } = request.nextUrl

      // Authenticated user on /login → redirect to dashboard. "Valid"
      // here means the cookie verifies — the backend session may still be
      // dead (idle-timed-out); a forced-logout arrival must render /login
      // or it loops back into the dead session.
      if (pathname === "/login" && !isForcedLogoutArrival(searchParams)) {
        return addSecurityHeaders(redirectToHome(request, { path: "/dashboard" }), nonce)
      }

      // `headers` carries the library's refreshed cookie; layer the nonce
      // and CSP onto it rather than starting from a fresh copy of the
      // request headers, so the rotated cookie isn't dropped.
      headers.set(NONCE_HEADER, nonce)
      headers.set("Content-Security-Policy", buildCsp(nonce))
      const response = NextResponse.next({ request: { headers } })
      return addSecurityHeaders(response, nonce)
    },

    handleInvalidToken: async (_reason) => {
      const { pathname } = request.nextUrl

      // Allow public paths without auth
      if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
        const passthroughHeaders = requestHeadersWithNonce(request, buildCsp(nonce), nonce)
        return addSecurityHeaders(
          NextResponse.next({ request: { headers: passthroughHeaders } }),
          nonce
        )
      }

      return addSecurityHeaders(
        redirectToLogin(request, { path: "/login", publicPaths: PUBLIC_PATHS }),
        nonce
      )
    },

    handleError: async (_error) => {
      return addSecurityHeaders(
        redirectToLogin(request, { path: "/login", publicPaths: PUBLIC_PATHS }),
        nonce
      )
    },
  })
}
