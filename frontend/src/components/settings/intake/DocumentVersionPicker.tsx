// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { usePublishedVersions } from "@/hooks/useIntakeDocuments"
import { NEWEST_VERSION, VERSION_PICKER_LABEL, versionChoice } from "./intakeCopy"

const NEWEST = "newest"

interface DocumentVersionPickerProps {
  documentKey: string
  /** The version the practice chose, or undefined for the newest. */
  chosen: string | undefined
  onChoose: (versionId: string | undefined) => void
  idPrefix: string
}

/**
 * Which wording of a document a consent question asks for.
 *
 * The newest published version is the default, and the one a practice
 * almost always wants: publishing the packet takes whatever is newest then.
 * An earlier published version can be chosen instead — to keep asking for
 * the wording people have been signing while a revision is reviewed. Only
 * published versions are offered; the server refuses anything else.
 *
 * Nothing shows for a document with one published version: there is no
 * choice to make.
 */
export function DocumentVersionPicker({ documentKey, chosen, onChoose, idPrefix }: DocumentVersionPickerProps) {
  const { data: versions } = usePublishedVersions(documentKey)
  if (!versions || versions.length < 2) return null
  const id = `${idPrefix}-document-version`
  const newest = versions[0]

  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{VERSION_PICKER_LABEL}</Label>
      <Select
        value={chosen ?? NEWEST}
        onValueChange={(value) => onChoose(value === NEWEST ? undefined : value)}
      >
        <SelectTrigger id={id} aria-label={VERSION_PICKER_LABEL}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NEWEST}>{NEWEST_VERSION(newest.version)}</SelectItem>
          {versions.map((v) => (
            <SelectItem key={v.id} value={v.id}>
              {versionChoice(v.version, v.published_at)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}
