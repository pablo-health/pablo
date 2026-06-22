// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { SidebarFooterConfig } from "./sidebarFooterConfig"

/**
 * Extension slot for the sidebar footer branding.
 *
 * Ships empty here. A downstream build (e.g. a deployment overlay) may
 * overwrite *this file only* to rebrand the footer; `sidebarFooterConfig.ts`
 * shallow-merges these over the base defaults at module load. Overriding only
 * the fields you care about means adding a new field to `SidebarFooterConfig`
 * never requires a downstream build to re-declare the existing branding — the
 * partial override is the single, stable seam.
 */
export const sidebarFooterConfigExtensions: Partial<SidebarFooterConfig> = {}
