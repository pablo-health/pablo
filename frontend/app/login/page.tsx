// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getAuthSurfaces } from "@/lib/auth/surfaces"

// Thin shell: render the active auth provider's login surface. Firebase
// renders its custom email/password + Google + TOTP screen; OIDC redirects
// to the IdP-hosted login.
export default function LoginPage() {
  const { LoginScreen } = getAuthSurfaces()
  return <LoginScreen />
}
