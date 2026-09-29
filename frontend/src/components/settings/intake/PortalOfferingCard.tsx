// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { SettingsCard, SettingsRow, Toggle } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { usePortalSettings, useSavePortalSettings } from "@/hooks/usePortalSettings"

/**
 * Whether the practice offers its clients the portal at all.
 *
 * The first card on the page, because every other portal setting only
 * matters once this is on. Turning it off asks first: it ends every client's
 * access at once, and they can sign in again only once it is back on.
 */
export function PortalOfferingCard() {
  const { data: settings } = usePortalSettings()
  const save = useSavePortalSettings()
  const [confirmingOff, setConfirmingOff] = useState(false)

  if (!settings) return null

  function change(enabled: boolean) {
    if (!enabled) {
      setConfirmingOff(true)
      return
    }
    save.mutate({ enabled: true })
  }

  function turnOff() {
    save.mutate({ enabled: false }, { onSettled: () => setConfirmingOff(false) })
  }

  return (
    <>
      <SettingsCard flush>
        <SettingsRow
          label="Client portal"
          description="Where your clients sign in between visits."
        >
          <Toggle
            checked={settings.enabled}
            onChange={change}
            label="Client portal"
            disabled={save.isPending}
          />
        </SettingsRow>
      </SettingsCard>

      <Dialog open={confirmingOff} onOpenChange={(open) => !open && setConfirmingOff(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Turn off the client portal?</DialogTitle>
            <DialogDescription>
              Clients won&apos;t be able to sign in until you turn it back on.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmingOff(false)}
              disabled={save.isPending}
            >
              Cancel
            </Button>
            <Button type="button" variant="destructive" onClick={turnOff} disabled={save.isPending}>
              Turn off
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
