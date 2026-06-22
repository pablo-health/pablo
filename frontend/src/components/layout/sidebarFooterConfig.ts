// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { sidebarFooterConfigExtensions } from "./sidebarFooterConfig.extensions"

/**
 * Branding strings for `SidebarFooter`. `SidebarFooter.tsx` is never forked;
 * a downstream build rebrands by overriding fields in
 * `sidebarFooterConfig.extensions.ts`, which are shallow-merged over these
 * defaults below.
 */
export interface SidebarFooterConfig {
  label: string
  href: string
  linkText: string
  linkClassName: string
}

const sidebarFooterConfigBase: SidebarFooterConfig = {
  label: "Pablo · AGPL-3.0 ·",
  href: "https://github.com/pablo-health/pablo",
  linkText: "github.com/pablo-health/pablo",
  linkClassName: "underline hover:text-neutral-700 break-all",
}

export const sidebarFooterConfig: SidebarFooterConfig = {
  ...sidebarFooterConfigBase,
  ...sidebarFooterConfigExtensions,
}
