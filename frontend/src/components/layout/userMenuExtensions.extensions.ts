// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { UserMenuExtensions } from "./userMenuExtensions"

/**
 * User-menu merge slot.
 *
 * The base build ships this empty; a downstream deployment overlay overwrites
 * *this file only* to add account destinations to the header menu — exactly
 * like `sidebarExtensions.extensions.ts`. `UserMenuExtensions` is defined in
 * `userMenuExtensions.ts` so a replacement of this file can still import it.
 */
export const userMenuExtensions: UserMenuExtensions = {
  append: [],
}
