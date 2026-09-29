// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { SendFormsFlow } from "./intakeSend/SendFormsFlow"

export { sendableForms } from "./intakeSend/sendable"

/**
 * Send a client forms, or a portal invitation, from their chart.
 *
 * Opens the same choose → review → send steps a new client gets, so there
 * is one way to start an intake and one place its wording is reviewed.
 */
export function SendIntakeForm({ patientId }: { patientId: string }) {
  const [open, setOpen] = useState(false)

  return (
    <div data-testid="send-intake-form">
      <Button onClick={() => setOpen(true)} data-testid="send-intake-form-button">
        Send forms
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[520px]">
          {open && (
            <SendFormsFlow
              patientId={patientId}
              onDone={() => setOpen(false)}
              header={
                <DialogHeader>
                  <DialogTitle>Send forms</DialogTitle>
                  <DialogDescription>Choose what this client should do, then review it.</DialogDescription>
                </DialogHeader>
              }
            />
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
