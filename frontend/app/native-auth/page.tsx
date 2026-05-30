// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getAuthSurfaces } from "@/lib/auth/surfaces"

// Thin shell: render the active auth provider's companion-app sign-in
// handoff surface.
export default function NativeAuthPage() {
  const { NativeAuthScreen } = getAuthSurfaces()
  return <NativeAuthScreen />
}
