// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Conditionally mounts Auth.js `<SessionProvider>` only when the active
 * auth provider is `oidc`. This wrapper is imported by `components/providers.tsx`
 * and is a no-op when `NEXT_PUBLIC_AUTH_PROVIDER` is `firebase` or unset,
 * so the Firebase code path is byte-for-byte unchanged at runtime.
 */

import type { ReactNode } from "react"
import { SessionProvider } from "next-auth/react"

const IS_OIDC = process.env.NEXT_PUBLIC_AUTH_PROVIDER === "oidc"

export function OidcSessionProviderWrapper({ children }: { children: ReactNode }) {
  if (!IS_OIDC) {
    return <>{children}</>
  }
  return <SessionProvider>{children}</SessionProvider>
}
