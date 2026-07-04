// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OIDC implementation of route-protection middleware (Next.js Edge Runtime).
 * The shared `middleware.ts` delegates here through `@/lib/auth/middleware`
 * when `NEXT_PUBLIC_AUTH_PROVIDER=oidc`.
 *
 * Behaviour mirrors the Firebase middleware:
 *   - Public paths pass through unauthenticated.
 *   - Authenticated users hitting /login are redirected to /dashboard.
 *   - Unauthenticated requests to protected routes are redirected to /login.
 *   - Security headers (CSP, HSTS, etc.) are added on every response.
 *
 * Token check: Auth.js stores the encrypted session JWT in a cookie; `auth()`
 * from the config verifies and decrypts it inside the edge runtime.
 */

import { type NextRequest, NextResponse } from "next/server"
import { auth } from "./config"
import { isForcedLogoutArrival } from "@/lib/auth/forced-logout"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

const PUBLIC_PATHS = [
  "/login",
  "/native-auth",
  "/baa-acceptance",
  "/mfa-enrollment",
  "/api/config",
  "/api/auth",
]

/**
 * CSP for the OIDC path. Keycloak replaces Google/Firebase connect targets;
 * `frame-src` allows the Keycloak domain so any embedded frames work.
 * The Keycloak origin is inferred from `AUTH_KEYCLOAK_ISSUER` at build time;
 * fall back to allowing 'self' when the env is absent.
 */
const keycloakOrigin = (() => {
  try {
    const issuer = process.env.AUTH_KEYCLOAK_ISSUER
    return issuer ? new URL(issuer).origin : ""
  } catch {
    return ""
  }
})()

const CSP_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com data:",
  "img-src 'self' https: data:",
  `connect-src 'self' ${process.env.API_URL || ""} ${keycloakOrigin}`.replace(/\s+/g, " ").trim(),
  `frame-src 'self' ${keycloakOrigin}`.replace(/\s+/g, " ").trim(),
  "object-src 'none'",
  "base-uri 'self'",
].join("; ")

function addSecurityHeaders(response: NextResponse): NextResponse {
  response.headers.set("Content-Security-Policy", CSP_POLICY)
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

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname.startsWith(p))
}

/**
 * The protected handler, wrapped by Auth.js `auth()`.
 *
 * Wrapping (rather than calling `auth()` imperatively) is load-bearing: when
 * the session read triggers a Keycloak token rotation in the `jwt` callback,
 * the `auth()` wrapper writes the rotated token back to the cookie on the
 * outgoing response. A bare `await auth()` would refresh the token but drop
 * the new value, so the next request would refresh again with an
 * already-consumed refresh token — forcing a spurious re-login.
 *
 * NOTE: even with persistence, multiple session readers in one request
 * (middleware + SSR + the client session endpoint) can each attempt a
 * refresh. Keycloak realms with refresh-token rotation enabled should
 * disable it for this client (or accept reuse) so concurrent refreshes
 * don't invalidate each other. See the deployment notes.
 */
const handleProtected = auth((request) => {
  const { pathname } = request.nextUrl
  const session = request.auth as { error?: string } | null
  const isAuthenticated = !!session && !session.error

  if (isAuthenticated) {
    // Authenticated user on /login → redirect to dashboard. "Authenticated"
    // here means the cookie verifies — the backend session may still be
    // dead (idle-timed-out); a forced-logout arrival must render /login
    // or it loops back into the dead session.
    if (pathname === "/login" && !isForcedLogoutArrival(request.nextUrl.searchParams)) {
      const dashboardUrl = new URL("/dashboard", request.url)
      return addSecurityHeaders(NextResponse.redirect(dashboardUrl))
    }
    return addSecurityHeaders(NextResponse.next())
  }

  // Unauthenticated: allow public paths through.
  if (isPublicPath(pathname)) {
    return addSecurityHeaders(NextResponse.next())
  }

  // Redirect to login, preserving the requested path as `callbackUrl`.
  const loginUrl = new URL("/login", request.url)
  loginUrl.searchParams.set("callbackUrl", request.url)
  return addSecurityHeaders(NextResponse.redirect(loginUrl))
})

export default function oidcAuthMiddleware(request: NextRequest) {
  // Dev mode: skip auth entirely (no IdP configured), just add headers.
  // Done before `auth()` runs so it isn't invoked without a configured secret.
  if (IS_DEV_MODE) {
    return addSecurityHeaders(NextResponse.next())
  }
  // Delegate to the auth()-wrapped handler so cookie rotation is persisted.
  return (handleProtected as unknown as (req: NextRequest) => Promise<NextResponse>)(request)
}
