// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getAuthSurfaces } from "@/lib/auth/surfaces"

// Thin shell: render the active auth provider's email-action surface
// (verify email / reset password / etc.). Firebase emails link here; other
// providers host their own action pages.
export default function AuthActionPage() {
  const { AuthActionScreen } = getAuthSurfaces()
  return <AuthActionScreen />
}
