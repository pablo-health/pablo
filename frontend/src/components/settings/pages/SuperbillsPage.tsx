// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState, type FormEvent } from "react"
import { AlertCircle, Download, Receipt } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { usePatientList } from "@/hooks/usePatients"
import {
  fetchSuperbill,
  superbillFilename,
  SuperbillRefusedError,
  type SuperbillFinding,
} from "@/lib/api/superbills"
import { cn } from "@/lib/utils"
import { SettingsCard, StatusBlock } from "../ui"

/**
 * Billing > Superbills.
 *
 * Pick a client and a date range, generate, download. The document is
 * rendered from the client's claims for the period — every code and fee on
 * it was already put on a claim built from the session — so there is
 * nothing to configure here. When something an insurer needs is missing,
 * the route refuses and lists it; the list is shown as it came, with the
 * field each item lives in, so the person can go fix it.
 */
export function SuperbillsPage() {
  const { data: patientsData, isLoading: isLoadingPatients } = usePatientList({ page_size: 100 })
  const [patientId, setPatientId] = useState("")
  const [start, setStart] = useState("")
  const [end, setEnd] = useState("")
  const [isGenerating, setIsGenerating] = useState(false)
  const [findings, setFindings] = useState<SuperbillFinding[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [downloaded, setDownloaded] = useState<string | null>(null)

  const patients = patientsData?.data ?? []
  const canGenerate = Boolean(patientId && start && end) && !isGenerating

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canGenerate) return
    setIsGenerating(true)
    setFindings(null)
    setError(null)
    setDownloaded(null)
    try {
      const blob = await fetchSuperbill(patientId, start, end)
      const filename = superbillFilename(start, end)
      saveBlob(blob, filename)
      setDownloaded(filename)
    } catch (caught) {
      if (caught instanceof SuperbillRefusedError) {
        setFindings(caught.findings)
      } else {
        setError(caught instanceof Error ? caught.message : "The superbill could not be generated.")
      }
    } finally {
      setIsGenerating(false)
    }
  }

  return (
    <>
      <SettingsCard
        title="Superbills"
        description="An itemised receipt a client submits to their own insurer for out-of-network reimbursement. Rendered from the client's claims for the period; build a claim from each session first."
      >
        <form onSubmit={handleSubmit} className="space-y-4" aria-label="Generate a superbill">
          <div className="space-y-2">
            <Label htmlFor="superbill-patient">Client</Label>
            <select
              id="superbill-patient"
              value={patientId}
              onChange={(event) => setPatientId(event.target.value)}
              disabled={isLoadingPatients}
              className={cn(
                "border-input h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm shadow-xs outline-none",
                "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
                "disabled:cursor-not-allowed disabled:opacity-50",
              )}
            >
              <option value="">Select a client…</option>
              {patients.map((patient) => (
                <option key={patient.id} value={patient.id}>
                  {patient.last_name}, {patient.first_name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="superbill-start">From</Label>
              <Input
                id="superbill-start"
                type="date"
                value={start}
                max={end || undefined}
                onChange={(event) => setStart(event.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="superbill-end">To</Label>
              <Input
                id="superbill-end"
                type="date"
                value={end}
                min={start || undefined}
                onChange={(event) => setEnd(event.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={!canGenerate}>
              <Download className="h-4 w-4" aria-hidden="true" />
              {isGenerating ? "Generating…" : "Generate PDF"}
            </Button>
            {downloaded && (
              <span className="text-sm text-muted-foreground" role="status">
                Downloaded {downloaded}
              </span>
            )}
          </div>
        </form>
      </SettingsCard>

      {findings && (
        <SettingsCard>
          <StatusBlock
            icon={AlertCircle}
            tone="honey"
            title="The superbill was not generated"
            description="Everything below has to be on file before the document can be issued. Nothing is filled in or guessed on your behalf."
          >
            <ul className="mt-3 space-y-2" aria-label="What is missing">
              {findings.map((finding, index) => (
                <li key={`${finding.field ?? finding.code}-${index}`} className="text-sm text-foreground">
                  {finding.message}
                  {finding.field && (
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{finding.field}</span>
                  )}
                </li>
              ))}
            </ul>
          </StatusBlock>
        </SettingsCard>
      )}

      {error && (
        <SettingsCard>
          <StatusBlock icon={Receipt} tone="mute" title="Something went wrong" description={error} />
        </SettingsCard>
      )}
    </>
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
