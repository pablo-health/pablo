// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot for the portal's sections.
 *
 * Ships empty here. A downstream build may replace *this file only* to add
 * sections of its own to the portal, the same pattern as
 * `providers.extensions.tsx`. `modules.tsx` registers each entry after the
 * engine's own sections, in the order listed, so an added section gets a
 * tile on Home, a link in the navigation (when it has a `label`) and a page
 * at `/portal/{slug}/{id}`, exactly as the engine's do.
 *
 * An entry can be gated on a portal module through `module`, on anything
 * else through `Gate`, or both; see `PortalSlot`. An id the engine already
 * registered is ignored, so an extension cannot replace an engine section.
 *
 * Keep `"use client"`: this is part of the registry the browser fills, for
 * the reason `modules.tsx` gives.
 */

"use client"

import type { PortalSlot } from "./slots"

export const extensionPortalSlots: PortalSlot[] = []
