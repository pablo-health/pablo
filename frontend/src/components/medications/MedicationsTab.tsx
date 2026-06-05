// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MedicationsTab
 *
 * Chart tab listing a patient's medications with status badges, edit controls,
 * a one-click Discontinue shortcut that soft-deletes by flipping status, and
 * delete. History is preserved via status transitions — records are never
 * hard-deleted from this view.
 */

"use client"

import { useState } from "react"
import { Pill, PencilLine, Ban, Trash2 } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/Toast"
import { usePatientMedications, useDeleteMedication, useUpdateMedication } from "@/hooks/useMedications"
import { MedicationModal } from "./MedicationModal"
import type { Medication, MedicationStatus } from "@/types/medications"

interface MedicationsTabProps {
  patientId: string
}

const STATUS_BADGE: Record<
  MedicationStatus,
  { label: string; className: string }
> = {
  active: {
    label: "Active",
    className: "bg-green-100 text-green-800",
  },
  discontinued: {
    label: "Discontinued",
    className: "bg-neutral-100 text-neutral-600",
  },
  on_hold: {
    label: "On hold",
    className: "bg-yellow-100 text-yellow-800",
  },
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
}

export function MedicationsTab({ patientId }: MedicationsTabProps) {
  const { data, isLoading, error } = usePatientMedications(patientId)
  const { showToast } = useToast()
  const deleteMedication = useDeleteMedication()
  const updateMedication = useUpdateMedication()

  const [addOpen, setAddOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<Medication | null>(null)

  async function handleDiscontinue(med: Medication) {
    try {
      await updateMedication.mutateAsync({
        patientId,
        medicationId: med.id,
        data: {
          status: "discontinued",
          stopped_at: new Date().toISOString().slice(0, 10),
        },
      })
      showToast(`${med.drug_name} marked as discontinued.`, "success")
    } catch {
      showToast("Could not discontinue medication. Please try again.", "error")
    }
  }

  async function handleDelete(med: Medication) {
    if (
      typeof window !== "undefined" &&
      !window.confirm(
        `Remove ${med.drug_name} from the chart? This cannot be undone.`,
      )
    ) {
      return
    }
    try {
      await deleteMedication.mutateAsync({ patientId, medicationId: med.id })
      showToast(`${med.drug_name} removed.`, "success")
    } catch {
      showToast("Could not remove medication. Please try again.", "error")
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
        {error instanceof Error ? error.message : "Failed to load medications."}
      </p>
    )
  }

  const all = data?.data ?? []
  // Active first, then on_hold, then discontinued; within each group, most
  // recently started first, with undated medications last.
  const statusOrder: MedicationStatus[] = ["active", "on_hold", "discontinued"]
  const sorted = [...all].sort((a, b) => {
    const byStatus = statusOrder.indexOf(a.status) - statusOrder.indexOf(b.status)
    if (byStatus !== 0) return byStatus
    if (a.started_at && b.started_at) {
      return b.started_at.localeCompare(a.started_at)
    }
    // Dated medications sort ahead of undated ones.
    if (a.started_at) return -1
    if (b.started_at) return 1
    return 0
  })

  if (sorted.length === 0) {
    return (
      <>
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <Pill className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600">No medications recorded.</p>
          <Button size="sm" onClick={() => setAddOpen(true)}>
            Add medication
          </Button>
        </div>
        <MedicationModal
          patientId={patientId}
          open={addOpen}
          onOpenChange={setAddOpen}
        />
      </>
    )
  }

  return (
    <>
      <div className="space-y-4">
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setAddOpen(true)}>
            Add medication
          </Button>
        </div>

        <ul className="space-y-2">
          {sorted.map((med) => {
            const badge = STATUS_BADGE[med.status]
            const isDiscontinuing =
              updateMedication.isPending &&
              updateMedication.variables?.medicationId === med.id &&
              updateMedication.variables?.data?.status === "discontinued"
            const isDeleting =
              deleteMedication.isPending &&
              deleteMedication.variables?.medicationId === med.id

            return (
              <li
                key={med.id}
                className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5"
              >
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-sm text-neutral-900 truncate">
                      {med.drug_name}
                    </span>
                    <span className="text-xs text-neutral-500 shrink-0">
                      {med.dose}
                    </span>
                  </span>
                  <span className="text-xs text-neutral-400">
                    {med.started_at
                      ? `Started ${formatDate(med.started_at)}`
                      : "No start date"}
                  </span>
                  {med.status === "discontinued" && med.stop_reason && (
                    <span className="text-xs text-neutral-400 truncate">
                      Stopped: {med.stop_reason}
                    </span>
                  )}
                </span>

                <span className="flex shrink-0 items-center gap-2">
                  <span
                    className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${badge.className}`}
                  >
                    {badge.label}
                  </span>

                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title="Edit"
                    onClick={() => setEditTarget(med)}
                  >
                    <PencilLine className="h-3.5 w-3.5" />
                    <span className="sr-only">Edit {med.drug_name}</span>
                  </Button>

                  {med.status !== "discontinued" && (
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      title="Discontinue"
                      disabled={isDiscontinuing}
                      onClick={() => handleDiscontinue(med)}
                    >
                      <Ban className="h-3.5 w-3.5" />
                      <span className="sr-only">
                        Discontinue {med.drug_name}
                      </span>
                    </Button>
                  )}

                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title="Delete"
                    disabled={isDeleting}
                    onClick={() => handleDelete(med)}
                    className="text-red-500 hover:text-red-700 hover:bg-red-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    <span className="sr-only">Delete {med.drug_name}</span>
                  </Button>
                </span>
              </li>
            )
          })}
        </ul>
      </div>

      <MedicationModal
        patientId={patientId}
        open={addOpen}
        onOpenChange={setAddOpen}
      />

      {editTarget && (
        <MedicationModal
          patientId={patientId}
          open={!!editTarget}
          onOpenChange={(open) => {
            if (!open) setEditTarget(null)
          }}
          initialData={editTarget}
        />
      )}
    </>
  )
}
