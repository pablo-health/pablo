// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { NavItem } from "./sidebarExtensions"
import { useFeaturePredicate } from "@/lib/featureGates"

/**
 * Nav-item visibility slot.
 *
 * Returns a predicate `Sidebar.tsx` uses to filter nav items.
 *
 * The base build answers `requiresFlag` from the deployment's own feature list
 * (`FEATURES_ENABLED`, via `useFeature`) — the same source the settings nav
 * reads, so there is one idea of "is this feature on" rather than two. It
 * ignores `requiresCapability`, having no notion of plans; a downstream build
 * overwrites *this file only* to add that, and may call its own hooks here
 * since this runs inside the client `Sidebar` render.
 */
export function useNavVisibility(): (item: NavItem) => boolean {
  const isOn = useFeaturePredicate()
  return (item: NavItem) => isOn(item.requiresFlag)
}
