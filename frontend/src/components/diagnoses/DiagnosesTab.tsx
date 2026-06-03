// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * DiagnosesTab
 *
 * Chart tab listing a patient's recorded diagnostic determinations with their
 * confirmed ICD-10-CM codes, plus the on-ramp to record a new one. The tab is
 * shown to every clinical role — a diagnosis + billing code is needed by any
 * clinician who bills. `prominence` only tunes how prominently the structured
 * criterion checklist is surfaced; it defaults to "lite" and is configurable
 * per deployment.
 */

"use client"

import { Stethoscope } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/Toast"
import { DiagnosisList } from "./DiagnosisList"
import {
  RecordDiagnosisButton,
  type DiagnosisFormProminence,
} from "./RecordDiagnosisButton"
import { useDeleteDiagnosis, usePatientDiagnoses } from "@/hooks/useDiagnoses"
import type { DiagnosticAssessment } from "@/types/diagnoses"

interface DiagnosesTabProps {
  patientId: string
  prominence?: DiagnosisFormProminence
}

export function DiagnosesTab({ patientId, prominence }: DiagnosesTabProps) {
  const { data, isLoading, error } = usePatientDiagnoses(patientId)
  const { showToast } = useToast()
  const deleteDiagnosis = useDeleteDiagnosis()

  async function handleDelete(assessment: DiagnosticAssessment) {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this diagnosis? It will be removed from the chart.")
    ) {
      return
    }
    try {
      await deleteDiagnosis.mutateAsync({ assessmentId: assessment.id, patientId })
      showToast("Diagnosis deleted.", "success")
    } catch {
      showToast("Could not delete the diagnosis. Please try again.", "error")
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load diagnoses."}
      </p>
    )
  }

  const assessments = data?.data ?? []

  if (assessments.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <Stethoscope className="h-8 w-8 text-neutral-300" />
        <p className="text-sm text-neutral-600">No diagnoses recorded yet.</p>
        <RecordDiagnosisButton patientId={patientId} prominence={prominence} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <RecordDiagnosisButton patientId={patientId} prominence={prominence} />
      </div>
      <DiagnosisList
        assessments={assessments}
        onDelete={handleDelete}
        deletingId={
          deleteDiagnosis.isPending
            ? deleteDiagnosis.variables?.assessmentId
            : null
        }
      />
    </div>
  )
}
