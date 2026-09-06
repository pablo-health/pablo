// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RenderingProviderCard
 *
 * The clinician's own identifiers as a claim's rendering-provider loop
 * carries them: their type 1 NPI and their NUCC taxonomy code. These live on
 * the clinician profile, not the practice's billing row, and go out on every
 * claim the clinician renders.
 *
 * The taxonomy picker offers the common behavioral-health codes and a free
 * text entry for any other, rather than the whole NUCC table.
 */

"use client"

import { useState } from "react"
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
import { SettingsCard } from "@/components/settings/ui"
import { useUpdateProfessionalInfo } from "@/hooks/useProfessionalInfo"
import type { ProfessionalInfoUpdate } from "@/lib/api/users"
import { useSettingsSaved } from "./SettingsSavedContext"

export const TAXONOMY_CODES: { code: string; label: string }[] = [
  { code: "101YM0800X", label: "Counselor, Mental Health" },
  { code: "101YP2500X", label: "Counselor, Professional" },
  { code: "103T00000X", label: "Psychologist" },
  { code: "1041C0700X", label: "Clinical Social Worker" },
  { code: "363LP0808X", label: "Nurse Practitioner, Psychiatric/Mental Health" },
]

const OTHER = "other"

function pickerValueFor(code: string): string {
  if (!code) return ""
  return TAXONOMY_CODES.some((entry) => entry.code === code) ? code : OTHER
}

interface RenderingProviderCardProps {
  npiNumber: string | null
  taxonomyCode: string | null
}

export function RenderingProviderCard({ npiNumber, taxonomyCode }: RenderingProviderCardProps) {
  const update = useUpdateProfessionalInfo()
  const { flashSaved } = useSettingsSaved()
  const [npi, setNpi] = useState(npiNumber ?? "")
  const [taxonomy, setTaxonomy] = useState(taxonomyCode ?? "")
  const [picker, setPicker] = useState(() => pickerValueFor(taxonomyCode ?? ""))
  const [problem, setProblem] = useState<string | null>(null)

  const patch: ProfessionalInfoUpdate = {}
  if (npi.trim() && npi.trim() !== (npiNumber ?? "")) patch.npi_number = npi.trim()
  if (taxonomy.trim() && taxonomy.trim() !== (taxonomyCode ?? "")) {
    patch.taxonomy_code = taxonomy.trim().toUpperCase()
  }
  const isDirty = Object.keys(patch).length > 0

  function handlePick(next: string) {
    setPicker(next)
    setTaxonomy(next === OTHER ? "" : next)
  }

  function handleSave() {
    if (patch.npi_number && !/^\d{10}$/.test(patch.npi_number)) {
      setProblem("An NPI is ten digits.")
      return
    }
    if (patch.taxonomy_code && patch.taxonomy_code.length > 10) {
      setProblem("A taxonomy code is at most ten characters.")
      return
    }
    setProblem(null)
    update.mutate(patch, {
      onSuccess: () => flashSaved(),
      onError: (error) => {
        setProblem(error instanceof Error && error.message ? error.message : "Could not save.")
      },
    })
  }

  return (
    <SettingsCard
      title="You, on the claim"
      description="Your own identifiers, sent as the rendering provider on every claim for a visit you held."
    >
      <div className="space-y-4">
        <div className="grid gap-1.5 max-w-sm">
          <Label htmlFor="clinician-npi">Your NPI</Label>
          <Input
            id="clinician-npi"
            value={npi}
            onChange={(e) => setNpi(e.target.value)}
            inputMode="numeric"
            placeholder="10 digits"
          />
          <p className="text-[12.5px] text-muted-foreground">
            Your individual (type 1) NPI. A solo practice with no billing NPI files under this one.
          </p>
        </div>

        <div className="grid gap-1.5 max-w-sm">
          <Label htmlFor="taxonomy-code">Taxonomy code</Label>
          <Select value={picker || undefined} onValueChange={handlePick}>
            <SelectTrigger id="taxonomy-code" className="w-full">
              <SelectValue placeholder="Choose your specialty" />
            </SelectTrigger>
            <SelectContent>
              {TAXONOMY_CODES.map((entry) => (
                <SelectItem key={entry.code} value={entry.code}>
                  {entry.label} · {entry.code}
                </SelectItem>
              ))}
              <SelectItem value={OTHER}>Another code</SelectItem>
            </SelectContent>
          </Select>
          {picker === OTHER && (
            <Input
              aria-label="Taxonomy code"
              value={taxonomy}
              onChange={(e) => setTaxonomy(e.target.value)}
              placeholder="e.g. 106H00000X"
              maxLength={10}
              autoComplete="off"
            />
          )}
          <p className="text-[12.5px] text-muted-foreground">
            The NUCC specialty code some payers want on the rendering provider. Not every payer asks; a claim goes out without one.
          </p>
        </div>

        {problem && (
          <p role="alert" className="text-[12.5px] text-destructive">
            {problem}
          </p>
        )}

        {isDirty && (
          <Button size="sm" onClick={handleSave} disabled={update.isPending}>
            {update.isPending ? "Saving..." : "Save"}
          </Button>
        )}
      </div>
    </SettingsCard>
  )
}
