// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { IntakeStarter } from "@/types/intakeDocuments"
import { START_FROM_TEMPLATE } from "./intakeCopy"

interface StarterPickerProps {
  starters: IntakeStarter[]
  onPick: (key: string) => void
  busy?: boolean
}

/**
 * "Start from a template", beside "Add question" on a draft form.
 *
 * Two clicks: open the list, pick one. What picking does is the caller's —
 * the form editor asks the server for the practice's copy and adds its items
 * to the draft.
 */
export function StarterPicker({ starters, onPick, busy }: StarterPickerProps) {
  const [open, setOpen] = useState(false)

  if (starters.length === 0) return null

  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <FileText className="mr-1 h-4 w-4" aria-hidden="true" />
        {START_FROM_TEMPLATE}
      </Button>
      {open && (
        <ul aria-label={START_FROM_TEMPLATE} className="space-y-1">
          {starters.map((starter) => (
            <li key={starter.key}>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => {
                  setOpen(false)
                  onPick(starter.key)
                }}
              >
                {starter.title}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
