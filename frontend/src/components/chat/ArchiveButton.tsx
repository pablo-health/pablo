// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Archive affordance (§13.11). A single icon-button in the panel
 * header with a confirmation Dialog. For Phase 4 baseline we don't
 * vendor a full DropdownMenu primitive — archive is the only header
 * action, so a single button is right-sized.
 */

import { useState } from "react"
import { Archive } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface ArchiveButtonProps {
  disabled?: boolean
  onConfirm: () => void | Promise<void>
}

export function ArchiveButton({ disabled, onConfirm }: ArchiveButtonProps) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  async function handleConfirm() {
    setBusy(true)
    try {
      await onConfirm()
      setOpen(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        onClick={() => setOpen(true)}
        disabled={disabled}
        aria-label="Archive conversation"
        data-slot="chat-archive-button"
        className="text-neutral-500 hover:text-neutral-800"
      >
        <Archive className="size-4" />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Archive this conversation?</DialogTitle>
            <DialogDescription>
              The conversation will move out of your active list. You can find it
              again by enabling &ldquo;include archived&rdquo; in the conversation
              list. It is not deleted.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button type="button" onClick={handleConfirm} disabled={busy}>
              {busy ? "Archiving…" : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
