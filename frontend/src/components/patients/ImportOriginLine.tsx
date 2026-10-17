// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { Download } from "lucide-react"
import { useImportOrigin } from "@/components/settings/import/useImport"
import { LONG_DATE, formatDate } from "@/lib/dates"
import { sourceSystemName } from "@/lib/noteDisplay"
import type { ImportOrigin } from "@/lib/api/migration"

/** Where the line sends the reader: Import history, with this run opened. */
export function importRunHref(runId: string): string {
  return `/dashboard/settings/import?run=${encodeURIComponent(runId)}`
}

/**
 * "Imported from SimplePractice on October 17, 2026": the system and the day,
 * nothing about runs or ledgers. The day is when the import finished, which
 * is when the client appeared here; a run still in flight has no finish yet
 * and falls back to when it started. A client merged into one who was
 * already here reads the same way — their chart carries that import too,
 * and the run names them as merged.
 */
export function importOriginText(origin: ImportOrigin): string {
  const system = sourceSystemName(origin.source_system) ?? origin.source_system
  const day = formatDate(origin.finished_at ?? origin.started_at, LONG_DATE, "en-US")
  return `Imported from ${system} on ${day}`
}

/**
 * One line in the chart header for a client an import brought in, linking to
 * that import in history. Nothing for a client added by hand, and nothing
 * until the answer has loaded — a line that appeared and vanished would be
 * a claim the data had not made.
 */
export function ImportOriginLine({ patientId }: { patientId: string }) {
  const { data } = useImportOrigin(patientId)
  if (!data?.origin) return null
  return (
    <div className="flex items-center gap-2" data-testid="import-origin">
      <Download className="w-4 h-4" aria-hidden="true" />
      <Link href={importRunHref(data.origin.run_id)} className="underline underline-offset-2">
        {importOriginText(data.origin)}
      </Link>
    </div>
  )
}
