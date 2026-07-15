// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Analytics, AnalyticsEvent, UserTraits } from "./types"

/**
 * No-op analytics implementation.
 *
 * Drops all calls on the floor in production. In development, mirrors
 * them to `console.debug` so events can be seen firing without a real
 * provider wired.
 */
export class NoopAnalytics implements Analytics {
  identify(userId: string, traits?: UserTraits): void {
    if (process.env.NODE_ENV === "development") {
      console.debug("[analytics] identify", { userId, traits })
    }
  }

  track(event: AnalyticsEvent): void {
    if (process.env.NODE_ENV === "development") {
      console.debug("[analytics]", event.name, event.props ?? {})
    }
  }

  reset(): void {
    if (process.env.NODE_ENV === "development") {
      console.debug("[analytics] reset")
    }
  }
}
