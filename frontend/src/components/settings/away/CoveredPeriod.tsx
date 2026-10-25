// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Link from "next/link"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useCoverageActivity } from "@/hooks/useAwayPeriods"
import type { AwayPeriod } from "@/types/away"
import { day } from "./day"

/**
 * What an audit action reads as in the list. Matched on the action's
 * prefix; anything unlisted is still shown, as "Other activity", so the
 * list never hides a row the colleague's access produced.
 */
const ACTIVITY_LABELS: ReadonlyArray<[prefix: string, label: string]> = [
  ["patient_viewed", "Opened the record"],
  ["patient_updated", "Updated the record"],
  ["note_", "Worked on a note"],
  ["session_", "Worked on a session"],
  ["patient_message", "Messages"],
  ["refill_request", "Refill request"],
  ["appointment_", "Appointment"],
  ["patient_document", "Documents"],
  ["patient_medication", "Medications"],
]

function activityLabel(action: string): string {
  return ACTIVITY_LABELS.find(([prefix]) => action.startsWith(prefix))?.[1] ?? "Other activity"
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function Activity({ periodId }: { periodId: string }) {
  const { data, isLoading } = useCoverageActivity(periodId, true)
  if (isLoading || !data) return <p className="text-[12.5px] text-muted-foreground">Loading...</p>
  if (data.items.length === 0) {
    return (
      <p className="text-[12.5px] text-muted-foreground" data-testid={`coverage-activity-${periodId}`}>
        No activity on your clients&apos; records.
      </p>
    )
  }
  return (
    <ul className="space-y-1" data-testid={`coverage-activity-${periodId}`}>
      {data.items.map((item, i) => (
        <li key={`${item.at}-${i}`} className="text-[12.5px] text-foreground">
          <span className="text-muted-foreground">{when(item.at)}</span>
          {" · "}
          <Link href={`/dashboard/patients/${item.patient_id}`} className="underline">
            {item.client_name ?? "Client"}
          </Link>
          {" · "}
          {activityLabel(item.action)}
        </li>
      ))}
    </ul>
  )
}

/** A covered period that is over, and — on request — what the colleague did during it. */
export function CoveredPeriod({ period }: { period: AwayPeriod }) {
  const [open, setOpen] = useState(false)
  const name = period.covering_name ?? "Your colleague"
  return (
    <li
      data-testid={`covered-period-${period.id}`}
      className="space-y-2 border-t border-border py-3 first:border-t-0"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-foreground">
          {day(period.start_date)} – {day(period.end_date)}
          <span className="text-[12.5px] text-muted-foreground"> · {name} covered</span>
        </p>
        <Button
          size="sm"
          variant="outline"
          data-testid={`covered-period-show-${period.id}`}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Hide" : `What ${name} did`}
        </Button>
      </div>
      {open && <Activity periodId={period.id} />}
    </li>
  )
}
