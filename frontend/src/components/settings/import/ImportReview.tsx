// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * The preview of an uploaded export: what it will add, what it can't bring
 * across yet, the questions it needs answered, and Apply. Apply stays off
 * until every question has an answer — the server checks the same thing.
 */

import { useState } from "react"
import { Loader2 } from "lucide-react"
import {
  recordKey,
  type ImportDecisions,
  type ImportPreview,
  type ImportRunDetail,
} from "@/lib/api/migration"
import { ImportQuestions } from "./ImportQuestions"

interface Props {
  run: ImportRunDetail
  applying: boolean
  onApply: (decisions: ImportDecisions) => void
}

const EMPTY: ImportDecisions = { assignments: {}, duplicates: {}, providers: {}, practice: {} }

const LABELS: Record<string, [string, string]> = {
  contact: ["client", "clients"],
  note: ["note", "notes"],
  questionnaire: ["questionnaire", "questionnaires"],
  thread: ["message thread", "message threads"],
  upload: ["document", "documents"],
}

/** The same check the server runs before it will apply. */
export function unanswered(preview: ImportPreview, decisions: ImportDecisions): number {
  let missing = 0
  for (const group of preview.questions.same_name) {
    for (const r of group.records) {
      if (!decisions.assignments[recordKey(r.record_type, r.source_id)]) missing += 1
    }
  }
  for (const d of preview.questions.duplicates) {
    if (!decisions.duplicates[d.card_id]) missing += 1
  }
  for (const p of preview.questions.providers) {
    if (!decisions.providers[p.name]) missing += 1
  }
  return missing
}

function plural(type: string, n: number): string {
  const [one, many] = LABELS[type] ?? [type, `${type}s`]
  return `${n} ${n === 1 ? one : many}`
}

function Summary({ preview }: { preview: ImportPreview }) {
  const lines: string[] = []
  const unchanged: string[] = []
  for (const type of Object.keys(LABELS)) {
    const counts = preview.counts[type] ?? {}
    const fresh = (counts.new ?? 0) + (counts.changed ?? 0)
    if (fresh) lines.push(plural(type, fresh))
    if (counts.unchanged) unchanged.push(plural(type, counts.unchanged))
  }
  return (
    <div data-testid="import-summary">
      {lines.length ? (
        <p className="text-sm text-foreground">This export will add {lines.join(", ")}.</p>
      ) : (
        <p className="text-sm text-foreground">Nothing new since your last import.</p>
      )}
      {unchanged.length > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          Already here from an earlier import: {unchanged.join(", ")}.
        </p>
      )}
    </div>
  )
}

export function ImportReview({ run, applying, onApply }: Props) {
  const [decisions, setDecisions] = useState<ImportDecisions>(EMPTY)
  const preview = run.preview
  if (!preview) return null
  const open = unanswered(preview, decisions)
  const nothingNew = Object.values(preview.counts).every(
    (c) => !(c.new ?? 0) && !(c.changed ?? 0),
  )

  return (
    <div className="space-y-6">
      <Summary preview={preview} />

      {preview.cannot_land.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-foreground">Not coming across yet</h3>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
            {preview.cannot_land.map((c) => (
              <li key={`${c.what}-${c.reason}`}>{c.reason}</li>
            ))}
          </ul>
        </div>
      )}

      <ImportQuestions runId={run.id} preview={preview} decisions={decisions} onChange={setDecisions} />

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => onApply(decisions)}
          disabled={applying || open > 0 || nothingNew}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {applying && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          {applying ? "Importing" : "Import"}
        </button>
        {open > 0 && (
          <p className="text-xs text-muted-foreground">
            {open === 1 ? "1 question to answer." : `${open} questions to answer.`}
          </p>
        )}
      </div>
    </div>
  )
}
