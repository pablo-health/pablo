// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ArrowLeft, Calendar, Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { SetupStepHead } from "@/components/setup"
import type { ConfirmImportResult, ImportProposal, ProposedSeries } from "@/lib/api/scheduling"

const VISIBLE_ROWS = 5

function cadenceLabel(cadence: string): string {
  return cadence === "biweekly" ? "every 2 weeks" : cadence
}

const DAY_NAMES = [
  "Mondays",
  "Tuesdays",
  "Wednesdays",
  "Thursdays",
  "Fridays",
  "Saturdays",
  "Sundays",
]

function timeLabel(localStartTime: string): string {
  const [hourText, minuteText] = localStartTime.split(":")
  const hour = Number.parseInt(hourText ?? "", 10)
  if (Number.isNaN(hour)) return localStartTime
  const period = hour < 12 ? "AM" : "PM"
  const twelveHour = hour % 12 === 0 ? 12 : hour % 12
  return `${twelveHour}:${minuteText ?? "00"} ${period}`
}

function whenLabel(series: ProposedSeries): string {
  const day = DAY_NAMES[series.weekday] ?? "Weekdays"
  return `${day} · ${timeLabel(series.local_start_time)}`
}

interface CalendarReviewStepProps {
  proposal: ImportProposal | null
  checked: Record<string, boolean>
  onToggle: (candidateKey: string) => void
  expanded: boolean
  onToggleExpanded: () => void
  onBack: () => void
  onReviewAgain: () => void
  onConfirm: () => void
  confirming: boolean
  error: string | null
  result: ConfirmImportResult | null
  onFinish: () => void
}

export function CalendarReviewStep({
  proposal,
  checked,
  onToggle,
  expanded,
  onToggleExpanded,
  onBack,
  onReviewAgain,
  onConfirm,
  confirming,
  error,
  result,
  onFinish,
}: CalendarReviewStepProps) {
  if (result) {
    return (
      <div className="space-y-4 text-center">
        <h2 className="font-display text-2xl font-semibold text-neutral-900">
          {result.patients_created} client{result.patients_created === 1 ? "" : "s"} added
        </h2>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">
          {result.appointments_created} appointment{result.appointments_created === 1 ? "" : "s"}{" "}
          scheduled ahead. Read access ended when the import finished — Pablo asks again if you
          ever import a second time.
        </p>
        {result.skipped.length > 0 ? (
          <p className="mx-auto max-w-md text-sm text-amber-700">
            {result.skipped.length} chart{result.skipped.length === 1 ? "" : "s"} were created,
            but couldn&rsquo;t be scheduled — the times collided with something already booked.
            You can schedule them yourself from their chart.
          </p>
        ) : null}
        <Button onClick={onFinish} className="mt-2">
          <Calendar className="h-4 w-4" />
          Go to my calendar
        </Button>
      </div>
    )
  }

  if (!proposal) {
    return (
      <div className="space-y-4">
        <SetupStepHead
          eyebrow="Step 4 · you decide"
          title="Which of these are clients?"
          lede="Look at your week first — this list fills in once Pablo has scanned it."
        />
        <Button variant="ghost" size="sm" onClick={onReviewAgain}>
          <ArrowLeft className="h-4 w-4" />
          Back to your week
        </Button>
      </div>
    )
  }

  const total = proposal.series.length
  const visible = expanded ? proposal.series : proposal.series.slice(0, VISIBLE_ROWS)
  const hiddenCount = total - visible.length
  const checkedCount = proposal.series.filter((series) => checked[series.candidate_key]).length

  return (
    <div className="space-y-4">
      <SetupStepHead
        eyebrow="Step 4 · you decide"
        title="Which of these are clients?"
        lede={`These ${total} repeat on a weekly or biweekly rhythm. Check the ones that are clients. Uncheck standups, classes, and anything else that just happens to repeat.`}
      />

      <div className="flex flex-col">
        {visible.map((series) => (
          <label
            key={series.candidate_key}
            className="grid cursor-pointer grid-cols-[20px_1fr_auto] items-center gap-3 border-b border-border py-2.5 last:border-b-0"
          >
            <Checkbox
              checked={checked[series.candidate_key] ?? false}
              onCheckedChange={() => onToggle(series.candidate_key)}
              aria-label={series.summary}
            />
            <span>
              <span className="block text-sm font-medium text-neutral-900">{series.summary}</span>
              <span className="block text-xs tabular-nums text-muted-foreground">
                {whenLabel(series)} · {cadenceLabel(series.cadence)}
              </span>
            </span>
            <span className="whitespace-nowrap text-xs tabular-nums text-muted-foreground">
              {series.occurrences_ahead} ahead
            </span>
          </label>
        ))}
      </div>

      {hiddenCount > 0 || expanded ? (
        <button
          type="button"
          onClick={onToggleExpanded}
          className="pt-1 text-left text-sm font-medium text-muted-foreground underline underline-offset-2 hover:text-neutral-700"
        >
          {expanded
            ? `Hide the other ${total - VISIBLE_ROWS}`
            : `Show the other ${hiddenCount} — all look like weekly clients`}
        </button>
      ) : null}

      <p className="border-t border-border pt-3 text-xs text-muted-foreground">
        {
          "Pablo read your calendar once and kept nothing. If a client isn't in this list - someone you see monthly, or on a changing schedule - add them once you're in. It takes a minute."
        }
      </p>

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      <div className="flex items-center gap-2 border-t border-border pt-4">
        <Button variant="ghost" size="sm" onClick={onBack} disabled={confirming}>
          Back
        </Button>
        <span className="flex-1" />
        <Button onClick={onConfirm} disabled={confirming || checkedCount === 0}>
          {confirming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          {confirming
            ? "Adding…"
            : `Add ${checkedCount} client${checkedCount === 1 ? "" : "s"}`}
        </Button>
      </div>
    </div>
  )
}
