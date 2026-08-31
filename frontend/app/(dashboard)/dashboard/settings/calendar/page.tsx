// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Suspense } from "react"
import { notFound } from "next/navigation"
import { CalendarSetupWizard } from "@/components/calendar/connect/CalendarSetupWizard"
import { Skeleton } from "@/components/ui/skeleton"
import { useConfig } from "@/lib/config"

export default function CalendarSetupPage() {
  // The Settings link is hidden when the deployment hasn't enabled calendar,
  // but the route stays reachable by typing it. Without this the wizard would
  // happily walk someone into a consent screen on a deployment that never
  // meant to offer it. ConfigProvider blocks children until the config has
  // loaded, so this is a settled boolean rather than a loading race.
  const { googleCalendarEnabled } = useConfig()
  if (!googleCalendarEnabled) {
    notFound()
  }

  return (
    <div className="max-w-3xl">
      {/* The wizard reads the OAuth code out of the query string, so it
          needs a boundary to suspend behind. */}
      <Suspense fallback={<Skeleton className="h-96 w-full" />}>
        <CalendarSetupWizard />
      </Suspense>
    </div>
  )
}
