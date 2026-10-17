// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { useOptionalConfig } from "@/lib/config-provider"

// Deliberately loose: one "@", a dot in the domain, no whitespace and nothing
// that could break out of a mailto href. It only has to keep an obviously
// mistyped value from becoming a link nobody can use.
const PLAUSIBLE_EMAIL = /^[^\s@<>"'?&]+@[^\s@<>"'?&]+\.[^\s@<>"'?&]+$/

/**
 * The deployment's support address, or null when there is none to show.
 *
 * Null for unset, blank, or malformed values, so every caller can treat
 * "null" as "render nothing" instead of showing a link that goes nowhere.
 */
export function supportEmailFrom(value: string | null | undefined): string | null {
  const email = (value ?? "").trim()
  return PLAUSIBLE_EMAIL.test(email) ? email : null
}

export function supportMailto(email: string): string {
  return `mailto:${email}`
}

/**
 * The deployment's help-articles page, or null when there is none to link.
 *
 * Only an absolute http(s) URL counts, so a blank or mistyped value (or a
 * javascript: URL) never becomes a link.
 */
export function helpUrlFrom(value: string | null | undefined): string | null {
  const raw = (value ?? "").trim()
  if (!raw) return null
  try {
    const url = new URL(raw)
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null
  } catch {
    return null
  }
}

/**
 * The help-articles page this deployment configured (HELP_URL on the frontend
 * container, served by /api/config), or null. Read inside ConfigProvider only:
 * unlike the support address, no error screen needs it.
 */
export function useHelpUrl(): string | null {
  const config = useOptionalConfig()
  return config ? helpUrlFrom(config.helpUrl) : null
}

// One lookup per page load for screens rendered outside ConfigProvider.
let standaloneLookup: Promise<string | null> | null = null

function fetchSupportEmail(): Promise<string | null> {
  standaloneLookup ??= fetch("/api/config")
    .then((res) => (res.ok ? res.json() : null))
    .then((body: { supportEmail?: unknown } | null) =>
      supportEmailFrom(typeof body?.supportEmail === "string" ? body.supportEmail : null),
    )
    .catch(() => null)
  return standaloneLookup
}

/** Test seam: forget the standalone lookup between tests. */
export function _resetSupportEmailLookupForTests(): void {
  standaloneLookup = null
}

/**
 * The support address this deployment configured (SUPPORT_EMAIL on the
 * frontend container, served by /api/config), or null when it has none.
 *
 * Safe outside ConfigProvider: error fallbacks call this, and they must not
 * throw while showing that something else did. The root error boundary
 * replaces every provider, yet it is the screen where the address matters
 * most, so outside the provider the hook reads /api/config itself (null until
 * that answers, and null for good if it fails).
 */
export function useSupportEmail(): string | null {
  const config = useOptionalConfig()
  const [standalone, setStandalone] = useState<string | null>(null)

  useEffect(() => {
    if (config) return
    let live = true
    void fetchSupportEmail().then((email) => {
      if (live) setStandalone(email)
    })
    return () => {
      live = false
    }
  }, [config])

  return config ? supportEmailFrom(config.supportEmail) : standalone
}
