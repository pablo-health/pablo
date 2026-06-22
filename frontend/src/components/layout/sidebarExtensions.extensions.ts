// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { NavItem } from "./sidebarExtensions"

/**
 * Sidebar nav merge slot.
 *
 * The base build ships this empty; a downstream deployment overlay overwrites
 * *this file only* to customise the nav — exactly like `queryKeys.extensions.ts`.
 * `sidebarExtensions.ts` composes the base nav with these, so a base item added
 * upstream is never shadowed by a stale overlay copy.
 *
 * - `overrides`: per-href patches applied to base items (relabel / re-icon).
 * - `append`: extra items added after the base clinician nav (may carry
 *   `requiresCapability` / `requiresFlag` gates honoured by `useNavVisibility`).
 */
export interface NavExtensions {
  overrides: Record<string, Partial<Omit<NavItem, "href">>>
  append: NavItem[]
}

export const navExtensions: NavExtensions = {
  overrides: {},
  append: [],
}
