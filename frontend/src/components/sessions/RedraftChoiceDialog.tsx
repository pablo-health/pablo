// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asked before a note the clinician has edited is drafted again. Keeping the
 * edits is the default: the backend keeps every field the clinician changed
 * and redrafts the rest (`merge_kept_edits`). Replacing them is the one way
 * a redraft discards an edit, so it is only ever an explicit choice.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import type { RedraftEdits } from "@/types/notes"

interface Choice {
  value: RedraftEdits
  label: string
  description: string
}

export interface RedraftChoiceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (edits: RedraftEdits) => void
  /** Overrides the wording of the default choice, for what is being added. */
  keepLabel?: string
  keepDescription?: string
  confirmLabel?: string
  pending?: boolean
}

export function RedraftChoiceDialog({
  open,
  onOpenChange,
  onConfirm,
  keepLabel = "Keep my edits",
  keepDescription = "Fields you changed stay as you wrote them. The rest is redrafted.",
  confirmLabel = "Redraft",
  pending = false,
}: RedraftChoiceDialogProps) {
  const [edits, setEdits] = useState<RedraftEdits>("keep")
  const choices: Choice[] = [
    { value: "keep", label: keepLabel, description: keepDescription },
    {
      value: "replace",
      label: "Redraft everything",
      description: "The new draft replaces your edits.",
    },
  ]

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setEdits("keep")
        onOpenChange(next)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>You&apos;ve edited this note</DialogTitle>
          <DialogDescription>Choose what happens to your edits.</DialogDescription>
        </DialogHeader>
        <fieldset className="space-y-2">
          <legend className="sr-only">Your edits</legend>
          {choices.map((choice) => (
            <label
              key={choice.value}
              className="flex cursor-pointer gap-3 rounded-md border border-neutral-200 p-3 has-[:checked]:border-primary-500"
            >
              <input
                type="radio"
                name="redraft-edits"
                value={choice.value}
                checked={edits === choice.value}
                onChange={() => setEdits(choice.value)}
                className="mt-1"
              />
              <span>
                <span className="block text-sm font-medium text-neutral-900">{choice.label}</span>
                <span className="block text-sm text-neutral-600">{choice.description}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={() => onConfirm(edits)} disabled={pending}>
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
