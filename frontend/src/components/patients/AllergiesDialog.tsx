// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AllergiesDialog
 *
 * Records the chart's allergies in one of three states. "No known drug
 * allergies" is a choice of its own, never an empty list: a note has to be
 * able to say NKDA, and a list nobody filled in cannot mean that.
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
import { Input } from "@/components/ui/input"
import { useToast } from "@/components/ui/Toast"
import { useUpdateAllergies } from "@/hooks/usePatients"
import type { AllergyEntry, AllergyStatus, PatientResponse } from "@/types/patients"

const CHOICES: { value: AllergyStatus; label: string }[] = [
  { value: "not_recorded", label: "Not recorded" },
  { value: "nkda", label: "No known drug allergies" },
  { value: "recorded", label: "Allergies" },
]

const BLANK: AllergyEntry = { substance: "", reaction: "" }

interface AllergiesDialogProps {
  patient: PatientResponse
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AllergiesDialog({ patient, open, onOpenChange }: AllergiesDialogProps) {
  const updateAllergies = useUpdateAllergies()
  const { showToast } = useToast()
  // Mounted fresh each time it opens (see ChartFacts), so it starts from the chart.
  const [status, setStatus] = useState<AllergyStatus>(patient.allergy_status ?? "not_recorded")
  const [entries, setEntries] = useState<AllergyEntry[]>(
    patient.allergies?.length ? patient.allergies : [BLANK],
  )

  const filled = entries
    .map((e) => ({ ...e, substance: e.substance.trim(), reaction: e.reaction?.trim() || null }))
    .filter((e) => e.substance)
  const canSave = status !== "recorded" || filled.length > 0

  function setEntry(index: number, change: Partial<AllergyEntry>) {
    setEntries((current) => current.map((e, i) => (i === index ? { ...e, ...change } : e)))
  }

  async function save() {
    try {
      await updateAllergies.mutateAsync({
        patientId: patient.id,
        data: { status, allergies: status === "recorded" ? filled : [] },
      })
      onOpenChange(false)
    } catch {
      showToast("Could not save allergies. Please try again.", "error")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Allergies</DialogTitle>
          <DialogDescription>Notes use what you record here.</DialogDescription>
        </DialogHeader>

        <fieldset className="space-y-2">
          <legend className="sr-only">Allergy status</legend>
          {CHOICES.map((choice) => (
            <label key={choice.value} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="allergy-status"
                value={choice.value}
                checked={status === choice.value}
                onChange={() => setStatus(choice.value)}
              />
              {choice.label}
            </label>
          ))}
        </fieldset>

        {status === "recorded" && (
          <div className="space-y-2">
            {entries.map((entry, index) => (
              <div key={index} className="flex items-center gap-2">
                <Input
                  aria-label={`Allergy ${index + 1}`}
                  placeholder="Substance"
                  value={entry.substance}
                  onChange={(e) => setEntry(index, { substance: e.target.value })}
                  maxLength={120}
                />
                <Input
                  aria-label={`Reaction ${index + 1}`}
                  placeholder="Reaction (optional)"
                  value={entry.reaction ?? ""}
                  onChange={(e) => setEntry(index, { reaction: e.target.value })}
                  maxLength={200}
                />
                {entries.length > 1 && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setEntries((current) => current.filter((_, i) => i !== index))}
                    aria-label={`Remove allergy ${index + 1}`}
                  >
                    Remove
                  </Button>
                )}
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setEntries((current) => [...current, BLANK])}
            >
              Add another
            </Button>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!canSave || updateAllergies.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
