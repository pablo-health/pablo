// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Suspense } from "react"
import { CalendarSetupWizard } from "@/components/calendar/connect/CalendarSetupWizard"
import { Skeleton } from "@/components/ui/skeleton"

export default function CalendarSetupPage() {
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
