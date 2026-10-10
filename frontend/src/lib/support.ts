// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

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
 * The support address this deployment configured (SUPPORT_EMAIL on the
 * frontend container, served by /api/config), or null when it has none.
 *
 * Safe outside ConfigProvider: error fallbacks call this, and they must not
 * throw while showing that something else did.
 */
export function useSupportEmail(): string | null {
  return supportEmailFrom(useOptionalConfig()?.supportEmail)
}
