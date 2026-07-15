// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { NoopAnalytics } from "./noop"
import type { Analytics } from "./types"

/**
 * The analytics singleton.
 *
 * Swap this assignment to wire a real provider. Callers import this
 * constant directly; the interface is stable, so the swap is invisible
 * to every call site. A downstream build can shadow this module to
 * substitute its own provider.
 */
export const analytics: Analytics = new NoopAnalytics()

export type {
  Analytics,
  AnalyticsEvent,
  OnboardingEvent,
  StepId,
  UserTraits,
} from "./types"
