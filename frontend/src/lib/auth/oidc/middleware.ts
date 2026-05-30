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

export default async function oidcAuthMiddleware(request: NextRequest) {
  // Dev mode: skip auth, just add security headers.
  if (IS_DEV_MODE) {
    return addSecurityHeaders(NextResponse.next())
  }

  const { pathname } = request.nextUrl

  // Auth.js `auth()` in edge context reads and decrypts the session cookie.
  const session = await auth()
  const isAuthenticated = !!session && !(session as { error?: string }).error

  if (isAuthenticated) {
    // Authenticated user on /login → redirect to dashboard.
    if (pathname === "/login") {
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
}
