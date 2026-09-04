// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertCircle, Check } from "lucide-react"
import { Button } from "@/components/ui/button"
import { disconnectGoogleCalendar, getGoogleCalendarStatus } from "@/lib/api/scheduling"

const STATUS_QUERY_KEY = ["google-calendar", "status"]

/** Summary row in settings; the connect flow itself lives in the wizard. */
export function GoogleCalendarSettings() {
  const queryClient = useQueryClient()
  const [disconnectError, setDisconnectError] = useState<string | null>(null)
  const { data: status } = useQuery({
    queryKey: STATUS_QUERY_KEY,
    queryFn: getGoogleCalendarStatus,
  })

  const disconnect = useMutation({
    mutationFn: disconnectGoogleCalendar,
    onMutate: () => setDisconnectError(null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_QUERY_KEY }),
    onError: (err) => setDisconnectError(err instanceof Error ? err.message : "Could not disconnect."),
  })

  return (
    <div className="space-y-2">
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
              {status.last_synced_at && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Last synced {new Date(status.last_synced_at).toLocaleString()}
                </p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground">Not connected.</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Link href="/dashboard/settings/calendar">
            <Button variant="outline" size="sm">
              {status?.connected ? "Manage" : "Set up"}
            </Button>
          </Link>
          {status?.connected && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              {disconnect.isPending ? "Disconnecting..." : "Disconnect"}
            </Button>
          )}
        </div>
      </div>
      {disconnectError && (
        <p className="flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-4 w-4" />
          {disconnectError}
        </p>
      )}
    </div>
  )
}
