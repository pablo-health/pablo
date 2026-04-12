import { type NextRequest, NextResponse } from "next/server"
import { authMiddleware, redirectToLogin, redirectToHome } from "next-firebase-auth-edge"
import { authConfig, loginPath, logoutPath } from "@/lib/auth-config"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

const PUBLIC_PATHS = ["/login", "/native-auth", "/baa-acceptance", "/mfa-enrollment", "/api/config", "/api/auth/native"]

const CSP_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://apis.google.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' https: data:",
  `connect-src 'self' https://*.googleapis.com https://*.firebaseio.com https://*.cloudfunctions.net https://*.pablo.health ${process.env.API_URL || ""} wss://*.firebaseio.com`.replace(/\s+/g, " ").trim(),
  "frame-src 'self' https://*.firebaseapp.com https://accounts.google.com",
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
  return response
}

export default async function middleware(request: NextRequest) {
  // Dev mode: skip auth, just add security headers
  if (IS_DEV_MODE) {
    return addSecurityHeaders(NextResponse.next())
  }

  // Pass tenant ID dynamically so the library preserves firebase.tenant in all tokens.
  // Without this, the library's custom token exchange strips the tenant claim.
  const tenantId = request.cookies.get("X-Tenant-ID")?.value

  return authMiddleware(request, {
    loginPath,
    logoutPath,
    ...authConfig,
    ...(tenantId && { tenantId }),

    handleValidToken: async (_tokens, headers) => {
      const { pathname } = request.nextUrl

      // Authenticated user on /login → redirect to dashboard
      if (pathname === "/login") {
        return addSecurityHeaders(redirectToHome(request, { path: "/dashboard" }))
      }

      const response = NextResponse.next({ request: { headers } })
      return addSecurityHeaders(response)
    },

    handleInvalidToken: async (_reason) => {
      const { pathname } = request.nextUrl

      // Allow public paths without auth
      if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
        return addSecurityHeaders(NextResponse.next())
      }

      return addSecurityHeaders(
        redirectToLogin(request, { path: "/login", publicPaths: PUBLIC_PATHS })
      )
    },

    handleError: async (_error) => {
      return addSecurityHeaders(
        redirectToLogin(request, { path: "/login", publicPaths: PUBLIC_PATHS })
      )
    },
  })
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.).*)",
    "/api/login",
    "/api/logout",
  ],
}
