// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Companion download and availability extension slots.
 *
 * `isCompanionAvailable()` answers the deployment-wide questions (platform,
 * feature flag); `useCompanionAccess()` answers the per-account one — a
 * deployment may decide recording is configured per practice rather than
 * for everyone. `useCompanionDownloadUrl()` answers where the installer
 * lives — a deployment may host its own build rather than linking the
 * public homepage. The default build allows every account and points at
 * the public homepage; a downstream build replaces this file to consult
 * its own policy (same pattern as `api/client.extensions.ts`).
 *
 * UI-only: the components that consult this hide the companion affordances,
 * and the backend's recording policy hook (`_gate_recording`) refuses the
 * upload regardless of what the client showed.
 *
 * An implementation MAY be a hook and MAY require the app's providers
 * (React Query, runtime config). The default build's implementations
 * require nothing. Tests that render a consumer of a slot should use
 * test/renderWithProviders so a data-backed implementation stays
 * renderable.
 */

export function useCompanionAccess(): boolean {
  return true
}

/**
 * null means this deployment has no artifact to offer — the dialog renders
 * the button disabled with a short explanation instead of a dead link.
 */
export function useCompanionDownloadUrl(): string | null {
  return "https://pablo.health"
}
