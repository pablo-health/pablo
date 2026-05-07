// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMemo, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useComplianceItems,
  useComplianceTemplates,
  useCreateComplianceItem,
  useUpdateComplianceItem,
} from "@/hooks/useCompliance"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"

interface ComplianceWizardProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ComplianceWizard({ open, onOpenChange }: ComplianceWizardProps) {
  const { data: templates = [] } = useComplianceTemplates()
  const { data: items = [] } = useComplianceItems()

  const [stepIndex, setStepIndex] = useState(0)

  const orderedTemplates = useMemo(
    () => [...templates].sort((a, b) => a.sort_order - b.sort_order),
    [templates],
  )
  const template = orderedTemplates[stepIndex]
  const existing =
    template && !template.multi_instance
      ? items.find((i) => i.item_type === template.item_type)
      : undefined

  if (!template) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>All set</DialogTitle>
            <DialogDescription>
              You&apos;ve been through every reminder. Pablo&apos;s got the rest.
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end pt-4">
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          </div>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <WizardStep
          // Remount on step change so draft state initializes from props.
          key={template.item_type + (existing?.id ?? "new")}
          template={template}
          existing={existing}
          stepIndex={stepIndex}
          totalSteps={orderedTemplates.length}
          onAdvance={() => setStepIndex((i) => i + 1)}
        />
      </DialogContent>
    </Dialog>
  )
}

interface WizardStepProps {
  template: ComplianceTemplate
  existing: ComplianceItem | undefined
  stepIndex: number
  totalSteps: number
  onAdvance: () => void
}

function WizardStep({
  template,
  existing,
  stepIndex,
  totalSteps,
  onAdvance,
}: WizardStepProps) {
  const [dueDate, setDueDate] = useState(existing?.due_date ?? "")
  const [notes, setNotes] = useState(existing?.notes ?? "")
  const [customLabel, setCustomLabel] = useState(
    template.multi_instance ? "" : template.label,
  )

  const createItem = useCreateComplianceItem()
  const updateItem = useUpdateComplianceItem()

  const isSaving = createItem.isPending || updateItem.isPending
  const canSave = dueDate.length > 0 || notes.length > 0

  async function save() {
    const payload = {
      item_type: template.item_type,
      label:
        template.multi_instance && customLabel ? customLabel : template.label,
      due_date: dueDate || null,
      notes: notes || null,
    }
    if (existing) {
      await updateItem.mutateAsync({ id: existing.id, payload })
    } else {
      await createItem.mutateAsync(payload)
    }
    onAdvance()
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-display">{template.label}</DialogTitle>
        <DialogDescription>{template.description}</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 pt-2">
        <div className="text-xs text-neutral-500">
          Step {stepIndex + 1} of {totalSteps}
        </div>

        {template.multi_instance && (
          <div className="space-y-1">
            <Label htmlFor="compliance-label">Label</Label>
            <Input
              id="compliance-label"
              placeholder={`e.g. ${template.label} — vendor name`}
              value={customLabel}
              onChange={(e) => setCustomLabel(e.target.value)}
            />
          </div>
        )}

        <div className="space-y-1">
          <Label htmlFor="compliance-due">
            {template.cadence_days
              ? "Last completed / next due"
              : "Expiration date"}
          </Label>
          <Input
            id="compliance-due"
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
          />
          {template.cadence_days && (
            <p className="text-xs text-neutral-500">
              Renews every {template.cadence_days} days.
            </p>
          )}
        </div>

        <div className="space-y-1">
          <Label htmlFor="compliance-notes">Notes (optional)</Label>
          <Textarea
            id="compliance-notes"
            placeholder="License number, carrier, NPI, etc."
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
          />
        </div>
      </div>

      <div className="flex justify-between pt-4">
        <Button variant="ghost" onClick={onAdvance} disabled={isSaving}>
          Skip
        </Button>
        <Button onClick={save} disabled={!canSave || isSaving}>
          {existing ? "Update & next" : "Save & next"}
        </Button>
      </div>
    </>
  )
}
