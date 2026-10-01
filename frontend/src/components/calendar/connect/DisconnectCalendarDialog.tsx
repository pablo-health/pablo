// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

/**
 * Confirms a Google Calendar disconnect before it happens.
 *
 * Disconnecting revokes Pablo's access at Google and deletes what Pablo read
 * from the calendar (followed events, remembered answers about who they
 * were); appointments booked in Pablo stay, with their link to Google cleared.
 * The copy carries only the two things a clinician would not otherwise
 * expect: what Pablo removes, and that changes made in Google stop reaching
 * their sessions. The mechanics live in app/calendar_providers/disconnect.py.
 */
export function DisconnectCalendarDialog({
  open,
  onOpenChange,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Disconnect Google Calendar?</DialogTitle>
          <DialogDescription>
            Pablo will stop using your Google Calendar and remove what it read from it. Your
            sessions in Pablo stay, but changes made in Google won&rsquo;t reach them.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onOpenChange(false)
              onConfirm()
            }}
          >
            Disconnect
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
