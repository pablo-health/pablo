// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChartHistoryTab
 *
 * The client's history as chart fields: psychiatric, trauma, social, medical
 * and family history, cultural considerations and the substance-use
 * baseline. A note reads these as written, so what changed is edited here
 * (or accepted from a note) rather than carried forward in note text.
 */

"use client"

import { Skeleton } from "@/components/ui/skeleton"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { usePatientChartHistory } from "@/hooks/useChartHistory"
import { HistoryFieldRow } from "./HistoryFieldRow"

export function ChartHistoryTab({ patientId }: { patientId: string }) {
  const { data, isLoading, error } = usePatientChartHistory(patientId)
  const { readOnly } = useReadOnlyMode()

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load the history."}
      </p>
    )
  }

  return (
    <div className="space-y-6">
      {(data?.groups ?? []).map((group) => (
        <section key={group.key} aria-labelledby={`history-group-${group.key}`}>
          <h3
            id={`history-group-${group.key}`}
            className="mb-1 text-sm font-semibold uppercase tracking-wide text-neutral-500"
          >
            {group.label}
          </h3>
          <div className="divide-y divide-neutral-100">
            {group.fields.map((field) => (
              <HistoryFieldRow
                key={field.key}
                patientId={patientId}
                field={field}
                readOnly={readOnly}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}
