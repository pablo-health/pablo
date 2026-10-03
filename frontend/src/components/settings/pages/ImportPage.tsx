// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Practice > Import. Bring clients and notes across from another EHR.
 *
 * Upload a SimplePractice export (the folder it downloads, zipped). The
 * server reads it in the background and comes back with a preview: what it
 * will add, what can't come across yet, and any question it needs answered
 * first. Import lands it; history lists every run, and an applied run can be
 * undone. Uploading the same export again adds only what is new.
 */

import { useEffect, useState } from "react"
import Link from "next/link"
import { Loader2 } from "lucide-react"
import { useQueryClient } from "@tanstack/react-query"
import { SettingsCard } from "../ui"
import { ApiError } from "@/lib/api/client"
import {
  IMPORT_BUSY_STATES,
  applyImport,
  deleteImportArchive,
  startImport,
  undoImport,
  type ImportDecisions,
  type ImportRunDetail,
} from "@/lib/api/migration"
import { ImportHistory } from "../import/ImportHistory"
import { ImportReview } from "../import/ImportReview"
import { importKeys, useImportHistory, useImportRun } from "../import/useImport"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

const GENERIC_ERROR = "Something went wrong. Please try again."

function message(error: unknown): string {
  if (error instanceof ApiError && error.message) return error.message
  return GENERIC_ERROR
}

function Receipt({ run }: { run: ImportRunDetail }) {
  const people = usePeopleTerm()
  const counts = run.report?.counts ?? {}
  const clients = (counts.contact?.created ?? 0) + (counts.contact?.merged ?? 0)
  const notes = counts.note?.new ?? 0
  const skipped = run.report?.not_landed.length ?? 0
  return (
    <div role="status" data-testid="import-receipt" className="space-y-1 text-sm">
      <p className="text-foreground">
        Imported {clients} {clients === 1 ? people.one : people.many} and {notes}{" "}
        {notes === 1 ? "note" : "notes"}.
      </p>
      {skipped > 0 && (
        <p className="text-xs text-muted-foreground">
          {skipped === 1 ? "1 item stayed behind." : `${skipped} items stayed behind.`} History has the details.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        <Link href="/dashboard/patients" className="underline underline-offset-2">
          See your {people.many}
        </Link>
        . Upcoming appointments come from your calendar, not the export —{" "}
        <Link href="/dashboard/settings/calendar" className="underline underline-offset-2">
          connect it here
        </Link>
        .
      </p>
    </div>
  )
}

export function ImportPage() {
  const queryClient = useQueryClient()
  const people = usePeopleTerm()
  const history = useImportHistory()
  const [activeId, setActiveId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const active = useImportRun(activeId)
  const run = active.data

  // Coming back to the page picks up a run that is still being read or waiting for review.
  useEffect(() => {
    if (activeId || !history.data) return
    const open = history.data.runs.find(
      (r) => IMPORT_BUSY_STATES.has(r.state) || r.state === "previewed",
    )
    if (open) setActiveId(open.id)
  }, [activeId, history.data])

  const refresh = (id: string) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: importKeys.run(id) }),
      queryClient.invalidateQueries({ queryKey: importKeys.runs() }),
    ])

  async function handleFile(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const started = await startImport(file)
      queryClient.setQueryData(importKeys.run(started.id), started)
      setActiveId(started.id)
      await queryClient.invalidateQueries({ queryKey: importKeys.runs() })
    } catch (e) {
      setError(message(e))
    } finally {
      setUploading(false)
    }
  }

  async function handleApply(decisions: ImportDecisions) {
    if (!run) return
    setBusyId(run.id)
    setError(null)
    try {
      const applying = await applyImport(run.id, decisions)
      queryClient.setQueryData(importKeys.run(run.id), applying)
      await refresh(run.id)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusyId(null)
    }
  }

  async function handleUndo(runId: string, includeEdited: boolean) {
    setBusyId(runId)
    setError(null)
    try {
      await undoImport(runId, includeEdited)
      await refresh(runId)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusyId(null)
    }
  }

  async function handleDeleteArchive(runId: string) {
    setBusyId(runId)
    setError(null)
    try {
      await deleteImportArchive(runId)
      await refresh(runId)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusyId(null)
    }
  }

  const reading = run && IMPORT_BUSY_STATES.has(run.state)

  return (
    <div className="space-y-6">
      <SettingsCard
        title="Import from SimplePractice"
        description={`Upload your SimplePractice export to bring your ${people.many} and notes across. You can upload a newer export later; only what's new is added.`}
      >
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">
          {uploading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          {uploading ? "Uploading" : "Upload export (.zip)"}
          <input
            type="file"
            accept=".zip,application/zip"
            className="sr-only"
            data-testid="import-archive-input"
            disabled={uploading}
            onChange={(e) => {
              void handleFile(e.target.files?.[0])
              e.target.value = ""
            }}
          />
        </label>

        {error && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {error}
          </p>
        )}

        {run && (
          <div className="mt-6" data-testid="import-run">
            {reading && (
              <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                {run.state === "applying" ? "Importing. You can leave this page." : "Reading your export. You can leave this page."}
              </p>
            )}
            {run.state === "failed" && (
              <p role="alert" className="text-sm text-destructive">
                {run.error ?? GENERIC_ERROR}
              </p>
            )}
            {run.state === "previewed" && (
              <ImportReview run={run} applying={busyId === run.id} onApply={handleApply} />
            )}
            {run.state === "applied" && <Receipt run={run} />}
          </div>
        )}
      </SettingsCard>

      {history.data && history.data.runs.length > 0 && (
        <SettingsCard title="Import history" description="Every import, newest first.">
          <ImportHistory
            runs={history.data.runs}
            busyId={busyId}
            onUndo={handleUndo}
            onDeleteArchive={handleDeleteArchive}
          />
        </SettingsCard>
      )}
    </div>
  )
}
