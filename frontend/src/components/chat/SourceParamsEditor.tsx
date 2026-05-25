// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Inline editors for the source types that need shaped params, not a
 * bare toggle (PABLO-6x5.9). Rendered inside SourceChipDetail:
 *
 * - pasted_text → a textarea producing {content}
 * - patient_documents → all uploaded docs (true) or a picked subset
 *   ({document_ids: [...]})
 *
 * Both call ``onApply`` with a backend-valid SourceParams shape, so a
 * visible source can never send the boolean-vs-shape mismatch that
 * errored the turn before this change.
 */

import { useState } from "react"
import { Loader2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { usePatientDocuments } from "@/hooks/usePatientDocuments"
import type { SourceKey, SourceParams } from "@/lib/chat/types"

interface SourceParamsEditorProps {
  sourceKey: SourceKey
  patientId: string
  value: SourceParams | undefined
  onApply: (params: SourceParams) => void
}

export function SourceParamsEditor({
  sourceKey,
  patientId,
  value,
  onApply,
}: SourceParamsEditorProps) {
  if (sourceKey === "pasted_text") {
    return <PastedTextEditor value={value} onApply={onApply} />
  }
  if (sourceKey === "patient_documents") {
    return (
      <DocumentsEditor patientId={patientId} value={value} onApply={onApply} />
    )
  }
  return null
}

function PastedTextEditor({
  value,
  onApply,
}: {
  value: SourceParams | undefined
  onApply: (params: SourceParams) => void
}) {
  const initial =
    typeof value === "object" && value && typeof value.content === "string"
      ? value.content
      : ""
  const [content, setContent] = useState(initial)

  return (
    <div className="space-y-2">
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={6}
        placeholder="Paste text to include as context for this conversation…"
        className="w-full rounded border border-neutral-300 p-2 text-sm"
        data-testid="pasted-text-input"
      />
      <div className="flex justify-end">
        <Button type="button" size="sm" onClick={() => onApply({ content })}>
          Apply
        </Button>
      </div>
    </div>
  )
}

function DocumentsEditor({
  patientId,
  value,
  onApply,
}: {
  patientId: string
  value: SourceParams | undefined
  onApply: (params: SourceParams) => void
}) {
  const { data, isLoading } = usePatientDocuments(patientId)
  const documents = data?.data ?? []

  const initialIds =
    typeof value === "object" && value && Array.isArray(value.document_ids)
      ? value.document_ids
      : null
  const [mode, setMode] = useState<"all" | "specific">(
    initialIds ? "specific" : "all",
  )
  const [selectedIds, setSelectedIds] = useState<string[]>(initialIds ?? [])

  const toggle = (id: string) =>
    setSelectedIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )

  const apply = () => {
    if (mode === "all") {
      onApply(true)
    } else {
      onApply({ document_ids: selectedIds })
    }
  }

  return (
    <div className="space-y-3">
      <fieldset className="space-y-1.5">
        <label className="flex items-center gap-2 text-sm text-neutral-800">
          <input
            type="radio"
            name="patient-documents-mode"
            checked={mode === "all"}
            onChange={() => setMode("all")}
          />
          Include all uploaded documents
        </label>
        <label className="flex items-center gap-2 text-sm text-neutral-800">
          <input
            type="radio"
            name="patient-documents-mode"
            checked={mode === "specific"}
            onChange={() => setMode("specific")}
          />
          Select specific documents
        </label>
      </fieldset>

      {mode === "specific" ? (
        isLoading ? (
          <p className="flex items-center gap-1 text-sm text-neutral-500">
            <Loader2 className="size-3 animate-spin" /> Loading documents…
          </p>
        ) : documents.length === 0 ? (
          <p className="text-sm text-neutral-500">
            No documents uploaded for this patient yet.
          </p>
        ) : (
          <ul
            className="max-h-48 space-y-1 overflow-y-auto rounded border border-neutral-200 p-2"
            data-testid="patient-documents-picker"
          >
            {documents.map((doc) => (
              <li key={doc.id}>
                <label className="flex items-center gap-2 text-sm text-neutral-800">
                  <Checkbox
                    checked={selectedIds.includes(doc.id)}
                    onCheckedChange={() => toggle(doc.id)}
                  />
                  <span className="truncate">{doc.filename}</span>
                </label>
              </li>
            ))}
          </ul>
        )
      ) : null}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          onClick={apply}
          disabled={mode === "specific" && selectedIds.length === 0}
        >
          Apply
        </Button>
      </div>
    </div>
  )
}
