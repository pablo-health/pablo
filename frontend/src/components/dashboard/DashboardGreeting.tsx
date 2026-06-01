// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSyncExternalStore } from "react"
import { useUserTimeZone } from "@/hooks/usePreferences"

interface DashboardGreetingProps {
  name?: string | null
}

const emptySubscribe = () => () => {}

/**
 * True only after client hydration. Lets us read the browser clock without a
 * server/client markup mismatch: the server snapshot is `false`, so SSR and
 * the first client render agree, then it flips to `true` post-hydration.
 */
function useIsClient(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  )
}

/**
 * Dashboard header greeting + date.
 *
 * Rendered on the client (not in the server component that owns the page) so
 * the day and greeting reflect the clinician's timezone rather than the
 * server's UTC clock. Computing these server-side made the header show the
 * UTC day while the client panels below showed the local day — near midnight
 * the two disagreed.
 */
export function DashboardGreeting({ name }: DashboardGreetingProps) {
  const timeZone = useUserTimeZone()
  const isClient = useIsClient()
  const now = isClient ? new Date() : null
  const firstName = name?.split(" ")[0]

  return (
    <div>
      <h1 className="text-3xl font-display font-bold text-neutral-900">
        {now ? greetingFor(now, timeZone) : "Hello"}
        {firstName ? `, ${firstName}` : ""}
      </h1>
      <p className="text-neutral-600 mt-2">
        {now
          ? now.toLocaleDateString("en-US", {
              weekday: "long",
              month: "long",
              day: "numeric",
              year: "numeric",
              timeZone,
            })
          : " "}
      </p>
    </div>
  )
}

function greetingFor(now: Date, timeZone: string): string {
  const hour = Number(
    now.toLocaleString("en-US", {
      hour: "2-digit",
      hourCycle: "h23",
      timeZone,
    }),
  )
  if (hour < 12) return "Good morning"
  if (hour < 18) return "Good afternoon"
  return "Good evening"
}
