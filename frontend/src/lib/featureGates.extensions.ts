// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Per-account feature answers.
 *
 * The base build has none: every account in a deployment sees the same
 * features, decided by `FEATURES_ENABLED` on the container. A downstream build
 * replaces THIS FILE ONLY to answer per practice — the same pattern as
 * `companion.extensions.ts`.
 *
 * An implementation MAY be a hook and MAY require the app's providers. It must
 * report `resolved: false` while fetching rather than returning an empty map,
 * so `useFeature` knows to fall back to the deployment answer instead of
 * treating "not loaded yet" as "not for you".
 */

import type { AccountFeatures } from "./featureGates"

export function useAccountFeatures(): AccountFeatures {
  return { features: {}, resolved: true }
}
