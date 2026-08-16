// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot on the dashboard home, rendered between the greeting
 * and the panels.
 *
 * Renders nothing here. A downstream build may overwrite *this file
 * only* to surface deployment-specific notices in the home page's own
 * flow — e.g. a wind-down notice in a read-only deployment — so the
 * notice sits under the greeting rather than stacked above the page.
 *
 * Contract for replacements: this renders inside the home page for
 * every clinician visit, beneath the root providers (React Query and
 * auth context are in scope). Keep it self-gating — return null
 * whenever there is nothing to say — and keep its tests in the
 * replacing build.
 */
export function DashboardHomeSlot(): React.ReactNode {
  return null
}
