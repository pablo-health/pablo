// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check, Link2Off, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SetupStepHead } from "@/components/setup"
import type { GoogleCalendarStatus } from "@/lib/api/scheduling"

interface CalendarConnectStepProps {
  status: GoogleCalendarStatus | undefined
  /** Human summary of the current selection, so this step can say what
   * Google's permission screen is about to ask for. */
  selectionSummary: string
  connecting: boolean
  disconnecting: boolean
  error: string | null
  onConnect: () => void
  onDisconnect: () => void
}

export function CalendarConnectStep({
  status,
  selectionSummary,
  connecting,
  disconnecting,
  error,
  onConnect,
  onDisconnect,
}: CalendarConnectStepProps) {
  if (status?.connected) {
    return (
      <div className="space-y-4">
        <SetupStepHead
          eyebrow="Step 1"
          title="Google Calendar is connected"
          lede="Sessions you book in Pablo show up on the calendar below."
        />
        <div className="flex items-center justify-between rounded-lg border border-border px-4 py-3">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-neutral-900">
              <Check className="h-4 w-4 text-secondary-600" />
              {status.calendar_id ?? "Connected"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {status.write_target === "primary"
                ? "Your main calendar"
                : "A calendar Pablo made for your sessions"}
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={onDisconnect} disabled={disconnecting}>
            {disconnecting ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Link2Off className="mr-1 h-4 w-4" />
            )}
            Disconnect
          </Button>
        </div>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <SetupStepHead
        eyebrow="Step 1"
        title="Connect Google Calendar"
        lede="Sign in with Google so the sessions you book in Pablo show up on your calendar."
      />
      <p className="text-sm text-muted-foreground">
        Google&rsquo;s permission screen asks for exactly what you choose under Sessions and nothing
        else. Right now that is {selectionSummary}. You can change it in the next step before you
        connect.
      </p>
      <Button onClick={onConnect} disabled={connecting}>
        {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
        Connect Google Calendar
      </Button>
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
