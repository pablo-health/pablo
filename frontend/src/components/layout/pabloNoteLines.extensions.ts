// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { PabloNoteExtensions } from "./pabloNoteLines"

/**
 * Pablo note merge slot.
 *
 * The base build ships this empty; a downstream build may overwrite *this file
 * only* to append lines or hide a base line by id — exactly like
 * `sidebarExtensions.extensions.ts`. `pabloNoteLines.ts` composes the base lines
 * with these, so a line added upstream is never shadowed by a stale downstream
 * copy. `PabloNoteExtensions` is defined in `pabloNoteLines.ts` so a replacement of
 * this file can still import it.
 */
export const pabloNoteExtensions: PabloNoteExtensions = {
  append: [],
  hide: [],
}
