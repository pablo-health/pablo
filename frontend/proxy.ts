import { NextResponse, type NextRequest } from "next/server"
import { authProviderMiddleware } from "@/lib/auth/middleware"

const BUILD_ASSET_PREFIX = "/_next/static"
const BUILD_ASSET_METHODS = ["GET", "HEAD"]

// Route protection is delegated to the active auth provider
// (NEXT_PUBLIC_AUTH_PROVIDER, default "firebase"). See
// src/lib/auth/middleware.ts and the provider's middleware impl.
//
// Build assets under /_next/static are immutable files, so reads are the
// only thing that makes sense there. Anything else gets a 405 here rather
// than falling through to the server-action handler, which answers an
// unrecognized action id with a 500. Reads are passed through untouched —
// routing them through the auth provider would send asset loads to the
// login redirect.
export default function proxy(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith(BUILD_ASSET_PREFIX)) {
    return BUILD_ASSET_METHODS.includes(request.method)
      ? NextResponse.next()
      : new NextResponse(null, { status: 405, headers: { Allow: BUILD_ASSET_METHODS.join(", ") } })
  }
  return authProviderMiddleware(request)
}

export const config = {
  matcher: [
    // `__/` is reserved for the Firebase auth helper (/__/auth/*, /__/firebase/*),
    // proxied to the Firebase auth domain in next.config.ts. It must bypass
    // route protection or the OAuth handler 307s to /login and sign-in breaks.
    "/((?!_next/static|_next/image|favicon.ico|__/|.*\\.).*)",
    "/api/login",
    "/api/logout",
    "/_next/static/:path*",
  ],
}
