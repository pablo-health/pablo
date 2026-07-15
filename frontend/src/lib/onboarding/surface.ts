// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Selects the active onboarding surface (the ordered registry of wizard
 * steps). The `/onboarding` router and the dashboard layout's
 * onboarding gate both resolve the surface through this function, the
 * same way `/login` and friends resolve their UI through
 * `getAuthSurfaces()`.
 *
 * The stock build returns the minimal surface. A downstream build that
 * ships a richer guided setup shadows this module to return its own
 * surface (typically selected off an edition env var), delegating to
 * {@link MINIMAL_ONBOARDING_SURFACE} for the default case.
 */

import { MINIMAL_ONBOARDING_SURFACE } from "./minimal"
import type { OnboardingSurface } from "./types"

export function getOnboardingSurface(): OnboardingSurface {
  return MINIMAL_ONBOARDING_SURFACE
}
