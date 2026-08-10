import { authProviderMiddleware } from "@/lib/auth/middleware"

// Route protection is delegated to the active auth provider
// (NEXT_PUBLIC_AUTH_PROVIDER, default "firebase"). See
// src/lib/auth/middleware.ts and the provider's middleware impl.
export default authProviderMiddleware

export const config = {
  matcher: [
    // `__/` is reserved for the Firebase auth helper (/__/auth/*, /__/firebase/*),
    // proxied to the Firebase auth domain in next.config.ts. It must bypass
    // route protection or the OAuth handler 307s to /login and sign-in breaks.
    "/((?!_next/static|_next/image|favicon.ico|__/|.*\\.).*)",
    "/api/login",
    "/api/logout",
  ],
}
