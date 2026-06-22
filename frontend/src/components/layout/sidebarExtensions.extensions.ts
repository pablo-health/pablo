// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { NavExtensions } from "./sidebarExtensions"

/**
 * Sidebar nav merge slot.
 *
 * The base build ships this empty; a downstream deployment overlay overwrites
 * *this file only* to customise the nav — exactly like `queryKeys.extensions.ts`.
 * `sidebarExtensions.ts` composes the base nav with these, so a base item added
 * upstream is never shadowed by a stale overlay copy. `NavExtensions` is defined
 * in `sidebarExtensions.ts` so a replacement of this file can still import it.
 */
export const navExtensions: NavExtensions = {
  overrides: {},
  append: [],
}
