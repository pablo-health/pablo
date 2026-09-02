// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Feature-gate slot.
 *
 * Answers "which unreleased surfaces may this account see?" for surfaces whose
 * answer is decided by the deployment rather than by the build — a downstream
 * build may grant a preview to one practice and not another, or turn a surface
 * on in one environment and off in the next.
 *
 * The default build has no such policy and returns an empty map, which means
 * every key falls through to the build-time flags in `featureFlags.ts`. A
 * downstream build replaces THIS FILE ONLY to consult its own source (same
 * pattern as `companion.extensions.ts` and `sidebarVisibility.ts`); it never
 * edits `featureGates.ts` or the registry.
 *
 * An implementation MAY be a hook and MAY require the app's providers (React
 * Query, runtime config). The default implementation requires nothing. Tests
 * that render a consumer should use test/renderWithProviders so a data-backed
 * implementation stays renderable.
 *
 * Loading is NOT the same as "off": an implementation that is still resolving
 * must report so via `resolved: false`, and `useFeatureGate` keeps a gated
 * surface hidden until it settles. Returning `{}` early would flash an
 * unreleased page on every page load.
 */

export interface FeatureGates {
  /** Key → allowed. A key that is absent falls through to the build flag. */
  gates: Record<string, boolean>
  /** False while an async implementation is still fetching. */
  resolved: boolean
}

export function useFeatureGates(): FeatureGates {
  return { gates: {}, resolved: true }
}
