// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { SettingsExtensions } from "./registry"

/**
 * Settings registry merge slot.
 *
 * The base build ships no additions. A downstream build replaces THIS FILE ONLY
 * to add its own groups and items, or to relabel a base item — it never copies
 * `registry.ts` and never forks the settings pages. Same merge-slot discipline
 * as `sidebarExtensions.extensions.ts`.
 */
export const settingsExtensions: SettingsExtensions = {
  overrides: {},
  appendItems: [],
  appendGroups: [],
}
