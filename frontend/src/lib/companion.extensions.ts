// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Companion download and availability extension slots.
 *
 * `isCompanionAvailable()` answers the deployment-wide questions (platform,
 * feature flag); `useCompanionAccess()` answers the per-account one — a
 * deployment may decide recording is configured per practice rather than
 * for everyone. `useCompanionDownloadUrl(platform)` answers where that
 * platform's installer lives — a deployment may host its own build. The
 * default build allows every account and links the published installers;
 * a downstream build replaces this file to consult its own policy (same
 * pattern as `api/client.extensions.ts`).
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

import type { CompanionPlatform } from "./companion"

export function useCompanionAccess(): boolean {
  return true
}

/**
 * The published installers: the Microsoft Store listing on Windows, which
 * installs and updates the app, and the latest signed release on macOS.
 */
const DEFAULT_DOWNLOAD_URLS: Record<CompanionPlatform, string> = {
  windows: "https://apps.microsoft.com/detail/9N0260HFX1JC",
  macos:
    "https://github.com/pablo-health/pablo-companion/releases/latest/download/Pablo.dmg",
}

/**
 * Where to download the desktop app for `platform`.
 *
 * null means there is no artifact to offer — the dialog renders the button
 * disabled with a short explanation instead of a dead link. A hook so a
 * downstream build can look its answer up; called unconditionally, so it
 * takes the null platform too.
 */
export function useCompanionDownloadUrl(
  platform: CompanionPlatform | null,
): string | null {
  return platform ? DEFAULT_DOWNLOAD_URLS[platform] : null
}
