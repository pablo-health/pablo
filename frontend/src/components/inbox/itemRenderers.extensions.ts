// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { InboxExtensions } from "./itemRenderers"

/**
 * Inbox merge slot.
 *
 * The base build ships this empty; a downstream deployment overwrites *this
 * file only* to add item kinds (a renderer per kind) and filters for them —
 * the same merge-slot discipline as `sidebarExtensions.extensions.ts`.
 * `itemRenderers.ts` composes the base renderers and filters with these, so a
 * kind added upstream is never shadowed by a stale downstream copy.
 */
export const inboxExtensions: InboxExtensions = {
  renderers: {},
  filters: [],
}
