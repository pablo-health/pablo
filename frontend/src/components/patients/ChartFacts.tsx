// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChartFacts
 *
 * The two clinical facts the chart header carries for every note: the
 * diagnoses (the active entries on the problem list, kept on the Problems
 * tab) and the allergies, recorded here. Both read "none recorded" rather
 * than disappearing, because an empty chart is something the reader should
 * see, not infer.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import type { PatientResponse } from "@/types/patients"
import { AllergiesDialog } from "./AllergiesDialog"

export function allergiesSummary(patient: PatientResponse): string {
  if (patient.allergy_status === "nkda") return "No known drug allergies"
  if (patient.allergy_status === "recorded" && patient.allergies?.length) {
    return patient.allergies
      .map((a) => (a.reaction ? `${a.substance} (${a.reaction})` : a.substance))
      .join(", ")
  }
  return "Not recorded"
}

export function ChartFacts({ patient }: { patient: PatientResponse }) {
  const [editing, setEditing] = useState(false)
  const { readOnly } = useReadOnlyMode()

  return (
    <>
      <div className="flex items-center gap-2 md:col-span-2" data-testid="chart-diagnoses">
        <span className="font-semibold">Diagnoses:</span>
        <span>{patient.diagnosis || "None recorded"}</span>
      </div>
      <div className="flex items-center gap-2 md:col-span-2" data-testid="chart-allergies">
        <span className="font-semibold">Allergies:</span>
        <span>{allergiesSummary(patient)}</span>
        {!readOnly && (
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
            Edit allergies
          </Button>
        )}
      </div>
      {editing && <AllergiesDialog patient={patient} open onOpenChange={setEditing} />}
    </>
  )
}
