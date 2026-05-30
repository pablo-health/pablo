// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * OIDC implementation of the `/mfa-enrollment` surface.
 *
 * Keycloak hosts TOTP and passkey enrollment on its own account-management
 * pages, so this surface is intentionally a no-op. When the OIDC provider is
 * active, callers that navigate to `/mfa-enrollment` are redirected to the
 * `returnTo` destination (if provided) or to `/dashboard`.
 *
 * This satisfies the `AuthSurfaces.MfaEnrollmentForm` contract while keeping
 * all credential management on the IdP.
 */

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import type { MfaEnrollmentFormProps } from "@/lib/auth/types"

export function OidcMfaEnrollmentForm({ returnTo }: MfaEnrollmentFormProps = {}) {
  const router = useRouter()

  useEffect(() => {
    const destination =
      returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//")
        ? returnTo
        : "/dashboard"
    router.replace(destination)
  }, [router, returnTo])

  // Render nothing — the redirect fires immediately on mount.
  return null
}
