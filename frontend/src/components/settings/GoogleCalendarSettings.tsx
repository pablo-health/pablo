// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useQuery } from "@tanstack/react-query"
import { Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getGoogleCalendarStatus } from "@/lib/api/scheduling"

/** Summary row in settings; the connect flow itself lives in the wizard. */
export function GoogleCalendarSettings() {
  const { data: status } = useQuery({
    queryKey: ["google-calendar", "status"],
    queryFn: getGoogleCalendarStatus,
  })

  return (
    <div className="flex items-center justify-between gap-4">
      <div className="text-sm">
        {status?.connected ? (
          <>
            <p className="flex items-center gap-2 font-medium text-neutral-900">
              <Check className="h-4 w-4 text-secondary-600" />
              {status.calendar_id ?? "Connected"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {status.write_target === "primary"
                ? "Your main calendar"
                : "A calendar Pablo made for your sessions"}
            </p>
          </>
        ) : (
          <p className="text-muted-foreground">Not connected.</p>
        )}
      </div>
      <Link href="/dashboard/settings/calendar">
        <Button variant="outline" size="sm">
          {status?.connected ? "Manage" : "Set up"}
        </Button>
      </Link>
    </div>
  )
}
