// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
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
import { useSchedulingPolicy } from "@/hooks/useSchedulingPolicy"

/** What each part is called where a practice chooses it. */
const MODULE_LABELS: Record<string, string> = {
  intake: "Forms",
  messaging: "Messages",
  documents: "Documents",
  appointments: "Appointments",
  refills: "Refill requests",
  billing: "Billing",
}

/**
 * Whether the practice offers its clients the portal, and what clients can
 * do in it.
 *
 * The first card on the page, because every other portal setting only
 * matters once this is on. Turning it off asks first: it ends every client's
 * access at once, and they can sign in again only once it is back on.
 *
 * The parts are the ones this deployment serves; a practice can turn any of
 * them off for its clients, but always keeps at least one. Booking is not a
 * part of its own here: seeing appointments is, and whether clients may book
 * one is the practice's scheduling policy, which lives with the rest of
 * scheduling — so the Appointments row says where that stands and links to
 * it, rather than keeping a second switch for the same answer.
 */
export function PortalOfferingCard() {
  const { data: settings } = usePortalSettings()
  const save = useSavePortalSettings()
  const [confirmingOff, setConfirmingOff] = useState(false)

  if (!settings) return null

  const modules = Object.entries(settings.modules)
  const onCount = modules.filter(([, on]) => on).length

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
        <SettingsRow label="Client portal" description="Where your clients sign in between visits.">
          <Toggle
            checked={settings.enabled}
            onChange={change}
            label="Client portal"
            disabled={save.isPending}
          />
        </SettingsRow>
        {modules.length > 0 && (
          <div data-testid="portal-modules">
            {modules.map(([name, on]) => (
              <SettingsRow
                key={name}
                nested
                label={MODULE_LABELS[name] ?? name}
                description={name === "appointments" ? <BookingLine /> : undefined}
              >
                <Toggle
                  checked={on}
                  onChange={(next) => save.mutate({ modules: { [name]: next } })}
                  label={MODULE_LABELS[name] ?? name}
                  // The last part on stays on: a portal with nothing in it
                  // is the portal switched off, and that has its own switch.
                  disabled={!settings.enabled || save.isPending || (on && onCount === 1)}
                />
              </SettingsRow>
            ))}
          </div>
        )}
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

/** What clients can do with appointments, from the scheduling policy. */
function BookingLine() {
  const { data: policy } = useSchedulingPolicy()
  const sentence = !policy?.self_book_existing
    ? "Clients can see their appointments."
    : policy.self_book_mode === "auto"
      ? "Clients can also book appointments."
      : "Clients can also request appointments."
  return (
    <span data-testid="portal-booking-line">
      {sentence}{" "}
      <Link href="/dashboard/settings/scheduling" className="font-medium underline">
        Booking settings
      </Link>
    </span>
  )
}
