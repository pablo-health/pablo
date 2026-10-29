// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SuperbillDialog
 *
 * The superbill for one client over a period, from their chart. The period
 * opens on last month, because a superbill is usually asked for once the
 * month's sessions are over; either date can be changed.
 *
 * Whether the diagnosis is printed is the client's own setting, saved on the
 * chart the moment it is changed, not a choice made again on each download.
 * A client who files with their insurer needs it every month; a client paying
 * out of pocket to keep therapy off their insurance record needs it never.
 * Off until someone turns it on.
 *
 * When something is missing the route refuses and lists it, and the list is
 * shown as it came so the clinician can go fix each item.
 */

"use client"

import { useState } from "react"
import { AlertCircle, Download } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { usePatient, useUpdatePatient } from "@/hooks/usePatients"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import {
  fetchSuperbill,
  superbillFilename,
  SuperbillRefusedError,
  type SuperbillFinding,
} from "@/lib/api/superbills"

interface SuperbillDialogProps {
  patientId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Today, for the default period. A prop so a test can pin it. */
  today?: Date
}

/** The first and last day of the month before ``today``, as ``YYYY-MM-DD``. */
export function lastMonth(today: Date): { start: string; end: string } {
  const first = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  const last = new Date(today.getFullYear(), today.getMonth(), 0)
  return { start: isoDate(first), end: isoDate(last) }
}

function isoDate(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0")
  const day = String(value.getDate()).padStart(2, "0")
  return `${value.getFullYear()}-${month}-${day}`
}

export function SuperbillDialog({ patientId, open, onOpenChange, today }: SuperbillDialogProps) {
  const people = usePeopleTerm()
  const patient = usePatient(patientId)
  const updatePatient = useUpdatePatient()
  const [period, setPeriod] = useState(() => lastMonth(today ?? new Date()))
  const [generating, setGenerating] = useState(false)
  const [findings, setFindings] = useState<SuperbillFinding[] | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const includesDiagnosis = Boolean(patient.data?.superbill_includes_diagnosis)

  function handleOpenChange(next: boolean) {
    if (!next) {
      setFindings(null)
      setProblem(null)
    }
    onOpenChange(next)
  }

  async function handleDiagnosis(checked: boolean) {
    setProblem(null)
    try {
      await updatePatient.mutateAsync({
        patientId,
        data: { superbill_includes_diagnosis: checked },
      })
    } catch {
      setProblem("The setting could not be saved. Try again.")
    }
  }

  async function handleGenerate() {
    setFindings(null)
    setProblem(null)
    if (!period.start || !period.end) {
      setProblem("Choose both dates.")
      return
    }
    if (period.end < period.start) {
      setProblem("The end date is before the start date.")
      return
    }
    setGenerating(true)
    try {
      saveBlob(
        await fetchSuperbill(patientId, period.start, period.end),
        superbillFilename(period.start, period.end),
      )
      handleOpenChange(false)
    } catch (caught) {
      if (caught instanceof SuperbillRefusedError) {
        setFindings(caught.findings)
      } else {
        setProblem("The superbill could not be generated.")
      }
    } finally {
      setGenerating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Superbill</DialogTitle>
          <DialogDescription>
            An itemized receipt of this {people.one}&rsquo;s visits and payments.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="superbill-from">From</Label>
              <Input
                id="superbill-from"
                type="date"
                value={period.start}
                onChange={(event) => setPeriod({ ...period, start: event.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="superbill-to">To</Label>
              <Input
                id="superbill-to"
                type="date"
                value={period.end}
                onChange={(event) => setPeriod({ ...period, end: event.target.value })}
              />
            </div>
          </div>

          <div className="flex items-start gap-2">
            <Checkbox
              id="superbill-diagnosis"
              checked={includesDiagnosis}
              onCheckedChange={(checked) => handleDiagnosis(checked === true)}
              disabled={!patient.data || updatePatient.isPending}
              className="mt-0.5"
            />
            <div>
              <Label htmlFor="superbill-diagnosis">Include the diagnosis</Label>
              <p className="text-xs text-neutral-500">
                For filing with an insurer. Remembered for this {people.one}.
              </p>
            </div>
          </div>

          {findings && (
            <div role="alert" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-medium">Before this superbill can be issued:</p>
              <ul className="mt-2 list-disc space-y-1 pl-5" aria-label="What is missing">
                {findings.map((finding, index) => (
                  <li key={`${finding.field ?? finding.code}-${index}`}>{finding.message}</li>
                ))}
              </ul>
            </div>
          )}
          {problem && (
            <p role="alert" className="flex items-start gap-2 text-sm text-red-600">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {problem}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleGenerate} disabled={generating}>
            <Download className="mr-2 h-4 w-4" />
            {generating ? "Preparing..." : "Download PDF"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
