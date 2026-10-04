// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Check, Link2Off, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { InfoPopover } from "@/components/ui/InfoPopover"
import { SetupStepHead } from "@/components/setup"
import type { GoogleCalendarStatus } from "@/lib/api/scheduling"
import { DisconnectCalendarDialog } from "./DisconnectCalendarDialog"

interface CalendarConnectStepProps {
  /** Where this step sits in the wizard's stepper, so the card and the
   * stepper always give the same number. */
  step: number
  status: GoogleCalendarStatus | undefined
  connecting: boolean
  disconnecting: boolean
  error: string | null
  onConnect: () => void
  onDisconnect: () => void
}

export function CalendarConnectStep({
  step,
  status,
  connecting,
  disconnecting,
  error,
  onConnect,
  onDisconnect,
}: CalendarConnectStepProps) {
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false)

  if (status?.connected) {
    return (
      <div className="space-y-4">
        <SetupStepHead
          eyebrow={`Step ${step}`}
          title="Google Calendar is connected"
          lede="Pablo adds sessions to the calendar below."
        />
        <div className="flex items-center justify-between rounded-lg border border-border px-4 py-3">
          <div>
            <p className="flex items-center gap-2 text-sm font-medium text-neutral-900">
              <Check className="h-4 w-4 text-secondary-600" />
              {status.calendar_name ?? status.calendar_id ?? "Connected"}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {status.write_target === "primary"
                ? "Your main calendar"
                : "A separate calendar for Pablo sessions"}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmingDisconnect(true)}
            disabled={disconnecting}
          >
            {disconnecting ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Link2Off className="mr-1 h-4 w-4" />
            )}
            Disconnect
          </Button>
        </div>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <DisconnectCalendarDialog
          open={confirmingDisconnect}
          onOpenChange={setConfirmingDisconnect}
          onConfirm={onDisconnect}
        />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <SetupStepHead
        eyebrow={`Step ${step}`}
        title="Connect Google Calendar"
        lede="Connect Google Calendar to add sessions and check for scheduling conflicts."
      />
      <div className="flex items-center gap-2">
        <Button onClick={onConnect} disabled={connecting}>
          {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          Continue with Google
        </Button>
        <InfoPopover label="About Google access">
          You&rsquo;ll choose which calendar Pablo can use and whether Pablo can check your busy
          times.
        </InfoPopover>
      </div>
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
