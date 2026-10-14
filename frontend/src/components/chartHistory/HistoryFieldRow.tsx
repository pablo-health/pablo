// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * HistoryFieldRow
 *
 * One chart-history field: its current text and where it came from, edited
 * in place. Removing is for a value that was never true; a value that has
 * changed is edited instead, so the earlier one stays as history. Both keep
 * what the field said, shown under "Earlier values".
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/Toast"
import { useRemoveHistoryField, useSetHistoryField } from "@/hooks/useChartHistory"
import type { HistoryField } from "@/types/chartHistory"

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

function sourceLine(field: HistoryField): string | null {
  if (!field.updated_at || field.text === null) return null
  const updated = `Last updated ${formatDate(field.updated_at)}`
  return field.source_note_date
    ? `${updated}, from the note of ${formatDate(field.source_note_date)}`
    : updated
}

interface HistoryFieldRowProps {
  patientId: string
  field: HistoryField
  readOnly: boolean
}

export function HistoryFieldRow({ patientId, field, readOnly }: HistoryFieldRowProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState("")
  const setField = useSetHistoryField()
  const removeField = useRemoveHistoryField()
  const { showToast } = useToast()
  const inputId = `history-${field.key}`
  const source = sourceLine(field)

  function startEditing() {
    setDraft(field.text ?? "")
    setEditing(true)
  }

  async function save() {
    const text = draft.trim()
    if (!text) return
    try {
      await setField.mutateAsync({ patientId, key: field.key, data: { text } })
      setEditing(false)
    } catch {
      showToast("Could not save. Please try again.", "error")
    }
  }

  async function remove() {
    if (
      typeof window !== "undefined" &&
      !window.confirm(`Remove ${field.label} from the chart? Use this for something entered in error.`)
    ) {
      return
    }
    try {
      await removeField.mutateAsync({ patientId, key: field.key })
    } catch {
      showToast("Could not remove. Please try again.", "error")
    }
  }

  return (
    <div className="space-y-1 py-2" data-testid={`history-field-${field.key}`}>
      <div className="flex items-start justify-between gap-3">
        <label htmlFor={inputId} className="text-sm font-medium text-neutral-900">
          {field.label}
        </label>
        {!readOnly && !editing && (
          <span className="flex shrink-0 gap-1">
            <Button variant="ghost" size="sm" onClick={startEditing}>
              {field.text === null ? "Add" : "Edit"}
              <span className="sr-only"> {field.label}</span>
            </Button>
            {field.text !== null && (
              <Button
                variant="ghost"
                size="sm"
                onClick={remove}
                disabled={removeField.isPending}
                className="text-red-500 hover:bg-red-50 hover:text-red-700"
              >
                Remove
                <span className="sr-only"> {field.label}</span>
              </Button>
            )}
          </span>
        )}
      </div>

      {editing ? (
        <div className="space-y-2">
          <textarea
            id={inputId}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            autoFocus
            className="border-input focus-visible:border-ring focus-visible:ring-ring/50 flex min-h-[60px] w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={save} disabled={!draft.trim() || setField.isPending}>
              {setField.isPending ? "Saving…" : "Save"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <p
          className={
            field.text === null
              ? "text-sm text-neutral-400"
              : "whitespace-pre-line text-sm text-neutral-700"
          }
        >
          {field.text ?? "Not recorded"}
        </p>
      )}

      {source && <p className="text-xs text-neutral-400">{source}</p>}

      {field.earlier.length > 0 && (
        <details className="text-xs text-neutral-500">
          <summary className="cursor-pointer">Earlier values ({field.earlier.length})</summary>
          <ul className="mt-1 space-y-1 pl-3">
            {field.earlier.map((r) => (
              <li key={`${r.replaced_at}-${r.written_at}`}>
                <span className="whitespace-pre-line text-neutral-600">
                  {r.text ?? "Not recorded"}
                </span>{" "}
                <span className="text-neutral-400">
                  ({formatDate(r.written_at)} to {formatDate(r.replaced_at)})
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
