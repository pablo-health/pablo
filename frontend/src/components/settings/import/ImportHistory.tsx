// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Every import this practice has run, newest first, with what it brought in.
 * An applied run can be undone; an uploaded archive can be deleted before it
 * expires on its own.
 */

import { useState } from "react"
import type { ImportRunSummary } from "@/lib/api/migration"

interface Props {
  runs: ImportRunSummary[]
  busyId: string | null
  onUndo: (runId: string, includeEdited: boolean) => void
  onDeleteArchive: (runId: string) => void
}

const STATE_LABEL: Record<string, string> = {
  queued: "Waiting",
  previewing: "Reading the export",
  previewed: "Ready to review",
  applying: "Importing",
  applied: "Imported",
  undoing: "Undoing",
  undone: "Undone",
  failed: "Didn't finish",
}

function added(run: ImportRunSummary): string | null {
  const counts = run.counts ?? {}
  const n = (type: string) =>
    (counts[type]?.new ?? 0) + (counts[type]?.created ?? 0) + (counts[type]?.merged ?? 0)
  const clients = n("contact")
  const notes = n("note")
  if (!clients && !notes) return null
  return `${clients} ${clients === 1 ? "client" : "clients"}, ${notes} ${notes === 1 ? "note" : "notes"}`
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

function UndoControl({ run, busy, onUndo }: { run: ImportRunSummary; busy: boolean; onUndo: Props["onUndo"] }) {
  const [confirming, setConfirming] = useState(false)
  const [includeEdited, setIncludeEdited] = useState(false)
  if (!confirming) {
    return (
      <button type="button" className="text-xs font-medium underline underline-offset-2" onClick={() => setConfirming(true)}>
        Undo this import
      </button>
    )
  }
  return (
    <div className="mt-2 rounded-md border border-border p-2 text-xs">
      <p>This removes what this import added. Anything you have edited since stays unless you include it.</p>
      <label className="mt-1 flex items-center gap-2">
        <input type="checkbox" checked={includeEdited} onChange={(e) => setIncludeEdited(e.target.checked)} />
        Include records I edited
      </label>
      <div className="mt-2 flex gap-3">
        <button
          type="button"
          disabled={busy}
          className="font-medium text-destructive underline underline-offset-2 disabled:opacity-60"
          onClick={() => onUndo(run.id, includeEdited)}
        >
          Remove this import
        </button>
        <button type="button" className="underline underline-offset-2" onClick={() => setConfirming(false)}>
          Keep it
        </button>
      </div>
    </div>
  )
}

export function ImportHistory({ runs, busyId, onUndo, onDeleteArchive }: Props) {
  if (!runs.length) return null
  return (
    <ul className="divide-y divide-border" aria-label="Import history">
      {runs.map((run) => {
        const summary = added(run)
        return (
          <li key={run.id} className="py-3" data-testid="import-history-row">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium text-foreground">{STATE_LABEL[run.state] ?? run.state}</span>
              <span className="text-xs text-muted-foreground">{when(run.started_at)}</span>
            </div>
            {summary && <p className="text-xs text-muted-foreground">{summary}</p>}
            {run.error && <p className="text-xs text-destructive">{run.error}</p>}
            <div className="mt-1 flex flex-wrap items-center gap-3">
              {run.has_archive && run.archive_expires_at && (
                <>
                  <span className="text-xs text-muted-foreground">
                    Uploaded file is deleted {when(run.archive_expires_at)}.
                  </span>
                  <button
                    type="button"
                    disabled={busyId === run.id}
                    className="text-xs font-medium underline underline-offset-2"
                    onClick={() => onDeleteArchive(run.id)}
                  >
                    Delete it now
                  </button>
                </>
              )}
              {run.state === "applied" && (
                <UndoControl run={run} busy={busyId === run.id} onUndo={onUndo} />
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
