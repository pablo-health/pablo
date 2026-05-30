import { authProviderMiddleware } from "@/lib/auth/middleware"

// Route protection is delegated to the active auth provider
// (NEXT_PUBLIC_AUTH_PROVIDER, default "firebase"). See
// src/lib/auth/middleware.ts and the provider's middleware impl.
export default authProviderMiddleware

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.).*)",
    "/api/login",
    "/api/logout",
  ],
}
