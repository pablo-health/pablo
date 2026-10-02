// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSyncExternalStore } from "react"

const QUERY = "(prefers-reduced-motion: reduce)"

// Reads a client-only browser feature the same way OnboardingPasskeyForm
// reads WebAuthn support: false on the server, the real value once mounted,
// no setState-in-effect and no hydration mismatch. Follows the setting if it
// changes while the page is open.
function subscribe(onChange: () => void): () => void {
  const query = window.matchMedia(QUERY)
  query.addEventListener?.("change", onChange)
  return () => query.removeEventListener?.("change", onChange)
}

function snapshot(): boolean {
  return window.matchMedia(QUERY).matches
}

function serverSnapshot(): boolean {
  return false
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot)
}
