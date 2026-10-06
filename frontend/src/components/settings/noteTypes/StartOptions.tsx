// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText, LayoutTemplate } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import type { PracticeNoteTypeSpec } from "@/types/noteTypes"
import { ListRow, SettingsCard } from "../ui"
import { FieldMessages } from "./EditorParts"
import { importSpec } from "./importSpec"
import { NOTE_TYPE_TEMPLATES, type NoteTypeTemplate } from "./templates"

interface StartOptionsProps {
  onBlank: () => void
  onTemplate: (template: NoteTypeTemplate) => void
  onFromNotes: () => void
  onImport: (spec: PracticeNoteTypeSpec) => void
}

/** Ways to begin a new note type: blank, from a template, from your own notes, or from JSON. */
export function StartOptions({ onBlank, onTemplate, onFromNotes, onImport }: StartOptionsProps) {
  const [json, setJson] = useState("")
  const [importError, setImportError] = useState<string | null>(null)

  const open = (raw: string) => {
    const result = importSpec(raw)
    if ("error" in result) {
      setImportError(result.error)
      return
    }
    setImportError(null)
    onImport(result.spec)
  }

  const readFile = async (file: File | undefined) => {
    if (!file) return
    const contents = await file.text()
    setJson(contents)
    open(contents)
  }

  return (
    <SettingsCard title="Start a note type">
      <Button type="button" onClick={onBlank}>
        New note type
      </Button>

      <h3 className="mb-1 mt-5 text-[13px] font-semibold text-foreground">Start from a template</h3>
      <ul>
        {NOTE_TYPE_TEMPLATES.map((template) => (
          <ListRow key={template.id} icon={LayoutTemplate} title={template.spec.label} subtitle={template.spec.description}>
            <Button
              size="sm"
              variant="outline"
              aria-label={`Start from ${template.spec.label}`}
              onClick={() => onTemplate(template)}
            >
              Use
            </Button>
          </ListRow>
        ))}
      </ul>

      <h3 className="mb-1 mt-5 text-[13px] font-semibold text-foreground">Start from your notes</h3>
      <ul>
        <ListRow
          icon={FileText}
          title="From your notes"
          subtitle="Give a few of your notes, or describe them, and get a note type in the same shape."
        >
          <Button size="sm" variant="outline" aria-label="Start from your notes" onClick={onFromNotes}>
            Use
          </Button>
        </ListRow>
      </ul>

      <details className="mt-4">
        <summary className="cursor-pointer text-[13px] font-semibold text-foreground">Import JSON</summary>
        <div className="mt-3 space-y-2">
          <Textarea
            aria-label="Note type JSON"
            rows={6}
            className="font-mono text-xs"
            value={json}
            onChange={(e) => setJson(e.target.value)}
          />
          <FieldMessages messages={importError ? [importError] : []} />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" size="sm" variant="outline" disabled={!json.trim()} onClick={() => open(json)}>
              Open in editor
            </Button>
            <label className="text-[12.5px] text-muted-foreground">
              or choose a file{" "}
              <input
                type="file"
                accept="application/json,.json"
                aria-label="Note type JSON file"
                className="text-[12.5px]"
                onChange={(e) => void readFile(e.target.files?.[0])}
              />
            </label>
          </div>
        </div>
      </details>
    </SettingsCard>
  )
}
