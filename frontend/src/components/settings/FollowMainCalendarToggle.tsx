// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { CALENDAR_SETUP_PATH } from "@/components/calendar/connect/CalendarSetupWizard"
import {
  rememberFollowWanted,
  rememberImportPending,
} from "@/components/calendar/connect/importConsent"
import { setFollowMainCalendar } from "@/lib/api/outsideSessions"
import { importNeedsConsent, scanCalendarForImport } from "@/lib/api/scheduling"

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

/**
 * "Keep bringing in new sessions from this calendar". Following reads the
 * calendar's events, which is the "Look at my week" permission, so without
 * it the setting can't be turned on and offers that permission instead.
 */
export function FollowMainCalendarToggle({
  following,
  importGranted,
  onChanged,
}: {
  following: boolean
  importGranted: boolean
  onChanged: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const change = async (enabled: boolean) => {
    setSaving(true)
    setError(null)
    try {
      await setFollowMainCalendar(enabled)
      onChanged()
    } catch {
      setError("Could not save that. Try again in a moment.")
    } finally {
      setSaving(false)
    }
  }

  // The permission is asked for from the setup page, which finishes the
  // round trip, turns following on and shows the week it read.
  const askForAccess = async () => {
    setSaving(true)
    setError(null)
    try {
      const redirectUri = `${window.location.origin}${CALENDAR_SETUP_PATH}`
      const result = await scanCalendarForImport(redirectUri, browserTimeZone())
      if (importNeedsConsent(result)) {
        rememberImportPending()
        rememberFollowWanted()
        window.location.assign(result.auth_url)
        return
      }
      // Already granted after all: nothing stands in the way.
      await change(true)
    } catch {
      setError("Could not reach Google. Try again in a moment.")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-1.5 border-t border-border pt-2">
      <div className="flex items-start gap-2.5">
        <Checkbox
          id="settings-follow-main-calendar"
          checked={importGranted && following}
          disabled={saving || !importGranted}
          onCheckedChange={(value) => change(value === true)}
        />
        <label htmlFor="settings-follow-main-calendar" className="text-sm text-neutral-900">
          Keep bringing in new sessions from this calendar
        </label>
      </div>
      {!importGranted && (
        <div className="flex flex-wrap items-center gap-2 pl-6 text-xs text-muted-foreground">
          <span>This needs &ldquo;Look at my week&rdquo; access.</span>
          <Button variant="outline" size="sm" onClick={askForAccess} disabled={saving}>
            Allow access
          </Button>
        </div>
      )}
      {error && <p className="pl-6 text-xs text-red-600">{error}</p>}
    </div>
  )
}
