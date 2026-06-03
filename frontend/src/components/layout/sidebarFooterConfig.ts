// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Branding strings for `SidebarFooter` — the extension point for that
 * component. A downstream build overwrites *this file only* to rebrand the
 * footer; `SidebarFooter.tsx` itself is never forked.
 */
export interface SidebarFooterConfig {
  label: string
  href: string
  linkText: string
  linkClassName: string
}

export const sidebarFooterConfig: SidebarFooterConfig = {
  label: "Pablo · AGPL-3.0 ·",
  href: "https://github.com/pablo-health/pablo",
  linkText: "github.com/pablo-health/pablo",
  linkClassName: "underline hover:text-neutral-700 break-all",
}
