// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Every import this practice has run, newest first, with what it brought in.
 * An applied run can be undone; an uploaded archive can be deleted before it
 * expires on its own. An applied run also opens to the clients it brought in,
 * each a link to their chart; a chart's own "Imported from" line lands here
 * with its run already open.
 */

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { ImportClientState, ImportRunSummary } from "@/lib/api/migration"
import type { PeopleWords } from "@/lib/peopleTerm"
import { useImportRun } from "./useImport"

interface Props {
  runs: ImportRunSummary[]
  busyId: string | null
  /** The run a link asked for (`?run=<id>`): shown with its clients open. */
  openId?: string | null
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

function countAdded(run: ImportRunSummary, type: string): number {
  const counts = run.counts?.[type] ?? {}
  return (counts.new ?? 0) + (counts.created ?? 0) + (counts.merged ?? 0)
}

function added(run: ImportRunSummary): string | null {
  const clients = countAdded(run, "contact")
  const notes = countAdded(run, "note")
  if (!clients && !notes) return null
  return `${clients} ${clients === 1 ? "client" : "clients"}, ${notes} ${notes === 1 ? "note" : "notes"}`
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
}

/** How a client came in, beside the name. Nothing for one the import created: that is the ordinary case. */
function cameIn(state: ImportClientState, people: PeopleWords): string | null {
  if (state === "merged") return `added to a ${people.one} already here`
  if (state === "updated") return "updated"
  return null
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

/**
 * The clients an applied run brought in, read when opened and not before: the
 * list is one request per run, and most rows are never opened. Each client
 * who is still in the chart links to it; one deleted since is simply absent.
 */
function RunClients({ runId, open, onToggle }: { runId: string; open: boolean; onToggle: (open: boolean) => void }) {
  const people = usePeopleTerm()
  const run = useImportRun(open ? runId : null)
  const clients = run.data?.clients
  return (
    <details open={open} onToggle={(e) => onToggle(e.currentTarget.open)} className="mt-1 text-xs">
      <summary className="cursor-pointer font-medium underline underline-offset-2" data-testid="import-run-clients-toggle">
        {people.Many}
      </summary>
      {open && clients && clients.length === 0 && (
        <p className="mt-1 text-muted-foreground">
          Every {people.one} from this import has since been deleted.
        </p>
      )}
      {open && clients && clients.length > 0 && (
        <ul className="mt-1 space-y-0.5" data-testid="import-run-clients">
          {clients.map((c) => {
            const how = cameIn(c.state, people)
            return (
              <li key={c.patient_id} data-state={c.state}>
                <Link href={`/dashboard/patients/${c.patient_id}`} className="underline underline-offset-2">
                  {c.first_name} {c.last_name}
                </Link>
                {how && <span className="text-muted-foreground"> · {how}</span>}
              </li>
            )
          })}
        </ul>
      )}
    </details>
  )
}

function HistoryRow({
  run,
  busy,
  linked,
  onUndo,
  onDeleteArchive,
}: {
  run: ImportRunSummary
  busy: boolean
  /** This is the run a link asked for: open its clients and bring it into view. */
  linked: boolean
  onUndo: Props["onUndo"]
  onDeleteArchive: Props["onDeleteArchive"]
}) {
  // Open when a link asked for this run, until the reader says otherwise.
  const [toggled, setToggled] = useState<boolean | null>(null)
  const clientsOpen = toggled ?? linked
  const row = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (linked) row.current?.scrollIntoView({ block: "center" })
  }, [linked])
  const summary = added(run)
  return (
    <li ref={row} className="py-3" data-testid="import-history-row" data-run-id={run.id}>
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
              disabled={busy}
              className="text-xs font-medium underline underline-offset-2"
              onClick={() => onDeleteArchive(run.id)}
            >
              Delete it now
            </button>
          </>
        )}
        {run.state === "applied" && <UndoControl run={run} busy={busy} onUndo={onUndo} />}
      </div>
      {run.state === "applied" && countAdded(run, "contact") > 0 && (
        <RunClients runId={run.id} open={clientsOpen} onToggle={setToggled} />
      )}
    </li>
  )
}

export function ImportHistory({ runs, busyId, openId = null, onUndo, onDeleteArchive }: Props) {
  if (!runs.length) return null
  return (
    <ul className="divide-y divide-border" aria-label="Import history">
      {runs.map((run) => (
        <HistoryRow
          key={run.id}
          run={run}
          busy={busyId === run.id}
          linked={run.id === openId}
          onUndo={onUndo}
          onDeleteArchive={onDeleteArchive}
        />
      ))}
    </ul>
  )
}
