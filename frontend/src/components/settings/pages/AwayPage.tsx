// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useAwayPeriods, useEndAwayPeriod } from "@/hooks/useAwayPeriods"
import type { AwayPeriod } from "@/types/away"
import { AwayPeriodForm } from "../away/AwayPeriodForm"
import { SettingsBadge, SettingsCard } from "../ui"

/** `2026-10-27` as `Tue, Oct 27`, read as a calendar day rather than a UTC instant. */
function day(iso: string): string {
  const [year, month, date] = iso.split("-").map(Number)
  return new Date(year, month - 1, date).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  })
}

function PeriodRow({
  period,
  onEdit,
}: {
  period: AwayPeriod
  onEdit: () => void
}) {
  const end = useEndAwayPeriod()
  return (
    <li
      data-testid={`away-period-${period.id}`}
      className="flex flex-wrap items-center justify-between gap-3 border-t border-border py-3 first:border-t-0"
    >
      <div className="space-y-0.5">
        <p className="text-sm text-foreground">
          {day(period.start_date)} – {day(period.end_date)}
          {period.active && (
            <SettingsBadge className="ml-2">Away now</SettingsBadge>
          )}
        </p>
        <p className="text-[12.5px] text-muted-foreground">
          Back {day(period.return_date)}
          {period.covering_name ? ` · ${period.covering_name} covering` : ""}
          {" · "}
          {period.acknowledgment_enabled ? "Replying to clients" : "Not replying to clients"}
        </p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={onEdit}>
          Edit
        </Button>
        <Button
          size="sm"
          variant="ghost"
          data-testid={`away-end-${period.id}`}
          onClick={() => end.mutate(period.id)}
        >
          {period.active ? "End now" : "Remove"}
        </Button>
      </div>
    </li>
  )
}

/**
 * You > Away. The dates a clinician is away, and the reply their clients get
 * when they write through the portal meanwhile.
 */
export function AwayPage() {
  const { data } = useAwayPeriods()
  const [editing, setEditing] = useState<AwayPeriod | "new" | null>(null)
  if (!data) return null

  return (
    <SettingsCard
      title="Away"
      description="Clients who message you while you're away get a reply with the day you're back."
    >
      {editing ? (
        <AwayPeriodForm
          key={editing === "new" ? "new" : editing.id}
          period={editing === "new" ? undefined : editing}
          coveringOptions={data.covering_options}
          onDone={() => setEditing(null)}
        />
      ) : (
        <div className="space-y-3">
          {data.periods.length > 0 && (
            <ul data-testid="away-periods">
              {data.periods.map((period) => (
                <PeriodRow key={period.id} period={period} onEdit={() => setEditing(period)} />
              ))}
            </ul>
          )}
          <Button size="sm" data-testid="away-add" onClick={() => setEditing("new")}>
            Add away dates
          </Button>
        </div>
      )}
    </SettingsCard>
  )
}
