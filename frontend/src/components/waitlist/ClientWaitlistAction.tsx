// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClientWaitlistAction
 *
 * The client page's way onto the waitlist. Not on it: "Add to waitlist",
 * which opens the dialog. On it: "On your waitlist", which shows their place
 * and what they asked for, with a way to take them off or open the list.
 *
 * Only an active client waits (the server refuses anyone else), so for a
 * client on hold or no longer seen the button is disabled and says why.
 * An entry that already exists still shows, whatever the client's status.
 */

"use client"

import { useId, useState } from "react"
import Link from "next/link"
import { ListOrdered } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useToast } from "@/components/ui/Toast"
import { WAITLIST_SETTINGS_PATH } from "@/components/settings/paths"
import { usePatientWaitlist, useRemoveWaitlistEntry } from "@/hooks/useWaitlist"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import type { WaitlistEntry } from "@/types/waitlist"
import { AddToWaitlistDialog } from "./AddToWaitlistDialog"
import {
  ADD_ACTION,
  CLOSE,
  ON_LIST,
  OPEN_LIST,
  PAUSED,
  REMOVE_FROM_LIST,
  SAVE_FAILED,
  entrySummary,
  notActiveReason,
  placeLine,
} from "./waitlistCopy"

interface ClientWaitlistActionProps {
  patientId: string
  patientName: string
  patientStatus: string
}

function OnListDialog({
  entry,
  patientName,
  open,
  onOpenChange,
}: {
  entry: WaitlistEntry
  patientName: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const remove = useRemoveWaitlistEntry()
  const { showToast } = useToast()
  const { readOnly } = useReadOnlyMode()

  async function takeOff() {
    try {
      await remove.mutateAsync(entry.id)
      onOpenChange(false)
    } catch {
      showToast(SAVE_FAILED, "error")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="waitlist-entry-dialog">
        <DialogHeader>
          <DialogTitle>{patientName} is on your waitlist</DialogTitle>
          <DialogDescription>
            {placeLine(entry.position)}
            {entry.paused ? ` · ${PAUSED}` : ""}
          </DialogDescription>
        </DialogHeader>
        <p data-testid="waitlist-entry-summary" className="text-sm text-neutral-800">
          {entrySummary(entry)}
        </p>
        <DialogFooter className="gap-2 sm:justify-between">
          {!readOnly ? (
            <Button
              type="button"
              variant="ghost"
              onClick={takeOff}
              disabled={remove.isPending}
              data-testid="waitlist-remove"
            >
              {REMOVE_FROM_LIST}
            </Button>
          ) : (
            <span />
          )}
          <span className="flex gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {CLOSE}
            </Button>
            <Button asChild>
              <Link href={WAITLIST_SETTINGS_PATH}>{OPEN_LIST}</Link>
            </Button>
          </span>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function ClientWaitlistAction({
  patientId,
  patientName,
  patientStatus,
}: ClientWaitlistActionProps) {
  const { data } = usePatientWaitlist(patientId)
  const { readOnly } = useReadOnlyMode()
  const people = usePeopleTerm()
  const reasonId = useId()
  const [open, setOpen] = useState(false)

  // Nothing to show until we know whether they are on the list.
  if (!data) return null

  if (data.entry) {
    return (
      <>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setOpen(true)}
          data-testid="waitlist-on-list"
        >
          <ListOrdered className="h-4 w-4" />
          {ON_LIST}
        </Button>
        <OnListDialog
          entry={data.entry}
          patientName={patientName}
          open={open}
          onOpenChange={setOpen}
        />
      </>
    )
  }

  if (readOnly) return null

  if (patientStatus !== "active") {
    const reason = notActiveReason(people)
    return (
      <span title={reason}>
        <Button
          variant="outline"
          size="sm"
          disabled
          aria-describedby={reasonId}
          data-testid="waitlist-add"
        >
          <ListOrdered className="h-4 w-4" />
          {ADD_ACTION}
        </Button>
        <span id={reasonId} className="sr-only">
          {reason}
        </span>
      </span>
    )
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="waitlist-add">
        <ListOrdered className="h-4 w-4" />
        {ADD_ACTION}
      </Button>
      {open && (
        <AddToWaitlistDialog
          patientId={patientId}
          patientName={patientName}
          defaults={data}
          open={open}
          onOpenChange={setOpen}
        />
      )}
    </>
  )
}
