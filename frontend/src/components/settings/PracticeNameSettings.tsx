// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { useUpdateProfessionalInfo } from "@/hooks/useProfessionalInfo"

interface PracticeNameSettingsProps {
  currentName: string
  /** Only the practice owner may rename it; everyone else sees it read-only. */
  canEdit: boolean
}

/**
 * The practice's display name — what clients see in the portal and what
 * appears on forms Pablo produces. Distinct from the legal business name on
 * the billing profile, which is what insurers match against.
 *
 * Renaming never changes the signed BAA; signing it again under the new name
 * is a separate choice offered beside the agreement.
 */
export function PracticeNameSettings({ currentName, canEdit }: PracticeNameSettingsProps) {
  const [name, setName] = useState(currentName)
  const [saved, setSaved] = useState(false)
  const update = useUpdateProfessionalInfo()

  const trimmed = name.trim()
  const isDirty = trimmed !== currentName
  const canSave = canEdit && isDirty && trimmed.length > 0 && !update.isPending

  const handleSave = () => {
    setSaved(false)
    update.mutate({ practice_name: trimmed }, { onSuccess: () => setSaved(true) })
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-2 max-w-sm">
        <Label htmlFor="practice-name">Practice name</Label>
        <Input
          id="practice-name"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setSaved(false)
          }}
          readOnly={!canEdit}
          aria-readonly={!canEdit}
          autoComplete="organization"
        />
        {!canEdit && (
          <p className="text-[12.5px] text-muted-foreground">
            Only the practice owner can change this.
          </p>
        )}
      </div>
      {canEdit && isDirty && (
        <Button size="sm" onClick={handleSave} disabled={!canSave}>
          {update.isPending ? "Saving..." : "Save"}
        </Button>
      )}
      {saved && !isDirty && <p className="text-[12.5px] text-muted-foreground">Saved.</p>}
      {update.isError && (
        <p className="text-[12.5px] text-red-700" role="alert">
          The practice name could not be saved.
        </p>
      )}
    </div>
  )
}
