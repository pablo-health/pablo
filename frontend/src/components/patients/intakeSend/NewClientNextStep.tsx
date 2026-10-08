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
  portalNote,
}: {
  patient: PatientResponse
  onDone: () => void
  /** Said in place of the portal invitation, when the practice just chose not to offer one. */
  portalNote?: string
}) {
  return (
    <div data-testid="new-client-next-step">
      <SendFormsFlow
        patientId={patient.id}
        onDone={onDone}
        dismissLabel="Not now"
        chartHref={`/dashboard/patients/${patient.id}`}
        header={
          <DialogHeader>
            <DialogTitle>What should {patient.first_name} do next?</DialogTitle>
            <DialogDescription>
              {patient.first_name} {patient.last_name} is added.{" "}
              {portalNote ?? "Choose packets to send and whether to invite them to the portal."}
            </DialogDescription>
          </DialogHeader>
        }
      />
    </div>
  )
}
