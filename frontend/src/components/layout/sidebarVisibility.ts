// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { NavItem } from "./sidebarExtensions"

/**
 * Nav-item visibility slot.
 *
 * Returns a predicate `Sidebar.tsx` uses to filter nav items. The base build
 * shows every item; a downstream overlay overwrites *this file only* to gate
 * items carrying `requiresCapability` / `requiresFlag` on its own plan
 * capabilities and runtime flags (it can call its own hooks here, since this
 * runs inside the client `Sidebar` render).
 */
export function useNavVisibility(): (item: NavItem) => boolean {
  return () => true
}
