// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * OIDC implementation of the `/auth/action` surface.
 *
 * Firebase email-link actions (verify email, reset password, etc.) are
 * hosted on Firebase's own domain and are not applicable to the OIDC
 * provider. Keycloak handles all email actions on its own hosted pages.
 *
 * This shell satisfies the `AuthSurfaces.AuthActionScreen` contract while
 * doing nothing meaningful — any deep-link to `/auth/action` under the OIDC
 * provider is an unexpected request and should redirect to login.
 */

import { useEffect } from "react"
import { useRouter } from "next/navigation"

export function OidcAuthActionScreen() {
  const router = useRouter()

  useEffect(() => {
    // Action URLs are not issued by Keycloak to this path — redirect to
    // login so the user is not stranded on a no-op page.
    router.replace("/login")
  }, [router])

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
    </div>
  )
}
