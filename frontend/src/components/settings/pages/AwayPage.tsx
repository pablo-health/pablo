// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useAwayPeriods, useEndAwayPeriod } from "@/hooks/useAwayPeriods"
import type { AwayPeriod } from "@/types/away"
import { AwayPeriodForm } from "../away/AwayPeriodForm"
import { CoveredPeriod } from "../away/CoveredPeriod"
import { CoverageRequests } from "../away/CoverageRequests"
import { day } from "../away/day"
import { SettingsBadge, SettingsCard } from "../ui"

/** Where the request to cover stands, from the data: nobody covers until they accept. */
function coverageLine(period: AwayPeriod): string {
  if (!period.covering_name) return ""
  switch (period.coverage_status) {
    case "accepted":
      return ` · ${period.covering_name} covering`
    case "declined":
      return ` · ${period.covering_name} can't cover`
    default:
      return ` · Waiting for ${period.covering_name} to accept`
  }
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
        <p className="text-[12.5px] text-muted-foreground" data-testid={`away-coverage-${period.id}`}>
          Back {day(period.return_date)}
          {coverageLine(period)}
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
 * You > Away. The dates a clinician is away, the reply their clients get
 * when they write through the portal meanwhile, what a covering colleague
 * did once it is over, and colleagues' requests to be covered.
 */
export function AwayPage() {
  const { data } = useAwayPeriods()
  const [editing, setEditing] = useState<AwayPeriod | "new" | null>(null)
  if (!data) return null

  return (
    <>
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
            {data.recently_covered.length > 0 && (
              <div className="space-y-1 pt-2">
                <p className="text-sm font-medium text-foreground">While you were away</p>
                <ul data-testid="away-recently-covered">
                  {data.recently_covered.map((period) => (
                    <CoveredPeriod key={period.id} period={period} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </SettingsCard>
      <CoverageRequests />
    </>
  )
}
