// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Branding strings for `AuthFooter` — the extension point for that component.
 * A downstream build overwrites *this file only* to rebrand the auth footer;
 * `AuthFooter.tsx` itself is never forked.
 */
export interface AuthFooterConfig {
  label: string
  href: string
  linkText: string
}

export const authFooterConfig: AuthFooterConfig = {
  label: "Pablo · AGPL-3.0 ·",
  href: "https://github.com/pablo-health/pablo",
  linkText: "github.com/pablo-health/pablo",
}
