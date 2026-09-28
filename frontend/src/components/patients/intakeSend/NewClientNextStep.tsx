// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import type { PatientResponse } from "@/types/patients"
import { SendFormsFlow } from "./SendFormsFlow"

/**
 * The step after adding a client: what should they do?
 *
 * The same flow the chart's Send forms button opens, headed for a client who
 * was just added. "Not now" is a real answer — plenty of clients are added
 * before anybody knows what to send them — and the chart's Intake tab is
 * where it can be done later.
 */
export function NewClientNextStep({
  patient,
  onDone,
}: {
  patient: PatientResponse
  onDone: () => void
}) {
  return (
    <div data-testid="new-client-next-step">
      <DialogHeader className="mb-4">
        <DialogTitle>What should {patient.first_name} do next?</DialogTitle>
        <DialogDescription>
          {patient.first_name} {patient.last_name} is added. Choose forms to send and whether to
          invite them to the portal.
        </DialogDescription>
      </DialogHeader>
      <SendFormsFlow patientId={patient.id} onDone={onDone} dismissLabel="Not now" />
    </div>
  )
}
