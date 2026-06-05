// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useCreateMedication, useUpdateMedication } from "@/hooks/useMedications"
import type { Medication, MedicationStatus } from "@/types/medications"

interface MedicationModalProps {
  patientId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** When supplied, the modal opens in edit mode pre-filled with this record. */
  initialData?: Medication
}

const STATUS_LABELS: Record<MedicationStatus, string> = {
  active: "Active",
  discontinued: "Discontinued",
  on_hold: "On hold",
}

export function MedicationModal({
  patientId,
  open,
  onOpenChange,
  initialData,
}: MedicationModalProps) {
  const isEdit = !!initialData

  const [drugName, setDrugName] = useState(initialData?.drug_name ?? "")
  const [dose, setDose] = useState(initialData?.dose ?? "")
  const [status, setStatus] = useState<MedicationStatus>(
    initialData?.status ?? "active",
  )
  const [startedAt, setStartedAt] = useState(
    initialData?.started_at?.slice(0, 10) ?? "",
  )
  const [notes, setNotes] = useState(initialData?.notes ?? "")
  const [error, setError] = useState<string | null>(null)

  const createMedication = useCreateMedication()
  const updateMedication = useUpdateMedication()

  const isPending = createMedication.isPending || updateMedication.isPending

  function resetForm() {
    setDrugName(initialData?.drug_name ?? "")
    setDose(initialData?.dose ?? "")
    setStatus(initialData?.status ?? "active")
    setStartedAt(initialData?.started_at?.slice(0, 10) ?? "")
    setNotes(initialData?.notes ?? "")
    setError(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) resetForm()
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (!drugName.trim()) {
      setError("Drug name is required.")
      return
    }
    if (!dose.trim()) {
      setError("Dose is required.")
      return
    }

    try {
      if (isEdit) {
        await updateMedication.mutateAsync({
          patientId,
          medicationId: initialData.id,
          data: {
            drug_name: drugName.trim(),
            dose: dose.trim(),
            status,
            started_at: startedAt || null,
            notes: notes.trim() || null,
          },
        })
      } else {
        await createMedication.mutateAsync({
          patientId,
          data: {
            drug_name: drugName.trim(),
            dose: dose.trim(),
            status,
            started_at: startedAt || null,
            notes: notes.trim() || null,
          },
        })
      }
      onOpenChange(false)
      resetForm()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save medication. Please try again.",
      )
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit medication" : "Add medication"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="med-drug-name">Drug name *</Label>
            <Input
              id="med-drug-name"
              value={drugName}
              onChange={(e) => setDrugName(e.target.value)}
              placeholder="e.g. Sertraline"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="med-dose">Dose *</Label>
            <Input
              id="med-dose"
              value={dose}
              onChange={(e) => setDose(e.target.value)}
              placeholder="e.g. 50 mg daily"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="med-status">Status</Label>
            <Select
              value={status}
              onValueChange={(v) => setStatus(v as MedicationStatus)}
            >
              <SelectTrigger id="med-status" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(STATUS_LABELS) as MedicationStatus[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {STATUS_LABELS[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="med-started-at">Start date</Label>
            <Input
              id="med-started-at"
              type="date"
              value={startedAt}
              onChange={(e) => setStartedAt(e.target.value)}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="med-notes">Notes</Label>
            <textarea
              id="med-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Optional clinical notes"
              rows={3}
              className="border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 aria-invalid:border-destructive flex min-h-[60px] w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? "Saving…" : isEdit ? "Save changes" : "Add medication"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
