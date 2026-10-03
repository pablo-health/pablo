// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ArrowLeft, Calendar, Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SetupStepHead } from "@/components/setup"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import type { ConfirmImportResult, ImportProposal, ProposedSeries } from "@/lib/api/scheduling"
import { seenElsewhere, WhichClientsList } from "./WhichClientsList"

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
  /** Where this step sits in the wizard's stepper, so the card and the
   * stepper always give the same number. */
  step: number
  proposal: ImportProposal | null
  checked: Record<string, boolean>
  onToggle: (candidateKey: string) => void
  /** The existing client each series is; null for a new client. */
  clientFor: Record<string, string | null>
  onChooseClient: (candidateKey: string, patientId: string | null) => void
  /** Series marked as not a client; remembered on confirm. */
  notClient: Record<string, boolean>
  onToggleNotClient: (candidateKey: string) => void
  expanded: boolean
  onToggleExpanded: () => void
  onBack: () => void
  onReviewAgain: () => void
  onConfirm: () => void
  confirming: boolean
  error: string | null
  result: ConfirmImportResult | null
  onFinish: () => void
  /** New sessions keep coming in from this calendar. */
  following?: boolean
}

export function CalendarReviewStep({
  step,
  proposal,
  checked,
  onToggle,
  clientFor,
  onChooseClient,
  notClient,
  onToggleNotClient,
  expanded,
  onToggleExpanded,
  onBack,
  onReviewAgain,
  onConfirm,
  confirming,
  error,
  result,
  onFinish,
  following = false,
}: CalendarReviewStepProps) {
  const people = usePeopleTerm()
  if (result) {
    return (
      <div className="space-y-4 text-center">
        <h2 className="font-display text-2xl font-semibold text-neutral-900">
          {result.patients_created} {result.patients_created === 1 ? people.one : people.many} added
        </h2>
        <p className="mx-auto max-w-md text-sm text-muted-foreground">
          {result.appointments_created} appointment{result.appointments_created === 1 ? "" : "s"}{" "}
          scheduled ahead.
          {/* Following keeps reading the calendar, so this would be false. */}
          {following
            ? null
            : " Read access ended when the import finished — Pablo asks again if you ever import a second time."}
        </p>
        {result.already_scheduled.map((key) => (
          <p key={key} className="mx-auto max-w-md text-sm text-muted-foreground">
            {proposal?.series.find((series) => series.candidate_key === key)?.summary ??
              "One series"}{" "}
            is already on your calendar.
          </p>
        ))}
        {result.skipped.length > 0 ? (
          <p className="mx-auto max-w-md text-sm text-amber-700">
            {result.skipped.length} couldn&rsquo;t be scheduled — the times collided with
            something already booked. You can schedule them yourself from their chart.
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
          eyebrow={`Step ${step} · you decide`}
          title={`Which of these are ${people.many}?`}
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
  const checkedCount = proposal.series.filter(
    (series) =>
      checked[series.candidate_key] &&
      !notClient[series.candidate_key] &&
      !seenElsewhere(series.match)
  ).length
  const notClientCount = proposal.series.filter((series) => notClient[series.candidate_key]).length
  // Only "not a client" answers to keep: nothing to add, still something to save.
  const savingOnly = checkedCount === 0 && notClientCount > 0
  const confirmLabel = confirming
    ? savingOnly
      ? "Saving…"
      : "Adding…"
    : savingOnly
      ? "Save"
      : `Add ${checkedCount} ${checkedCount === 1 ? people.one : people.many}`

  return (
    <div className="space-y-4">
      <SetupStepHead
        eyebrow={`Step ${step} · you decide`}
        title={`Which of these are ${people.many}?`}
        lede={`These ${total} repeat on a weekly or biweekly rhythm. Check the ones that are ${people.many}. Uncheck standups, classes, and anything else that just happens to repeat.`}
      />

      <WhichClientsList
        rows={visible.map((series) => ({
          key: series.candidate_key,
          title: series.summary,
          detail: `${whenLabel(series)} · ${cadenceLabel(series.cadence)}`,
          aside: `${series.occurrences_ahead} ahead`,
          match: series.match,
        }))}
        checked={checked}
        onToggle={onToggle}
        clientFor={clientFor}
        onChooseClient={onChooseClient}
        notClient={notClient}
        onToggleNotClient={onToggleNotClient}
      />

      {hiddenCount > 0 || expanded ? (
        <button
          type="button"
          onClick={onToggleExpanded}
          className="pt-1 text-left text-sm font-medium text-muted-foreground underline underline-offset-2 hover:text-neutral-700"
        >
          {expanded
            ? `Hide the other ${total - VISIBLE_ROWS}`
            : `Show the other ${hiddenCount} — all look like weekly ${people.many}`}
        </button>
      ) : null}

      <p className="border-t border-border pt-3 text-xs text-muted-foreground">
        {
          `If a ${people.one} isn't in this list - someone you see monthly, or on a changing schedule - add them once you're in. It takes a minute.`
        }
      </p>

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      <div className="flex items-center gap-2 border-t border-border pt-4">
        <Button variant="ghost" size="sm" onClick={onBack} disabled={confirming}>
          Back
        </Button>
        <span className="flex-1" />
        <Button
          onClick={onConfirm}
          disabled={confirming || (checkedCount === 0 && notClientCount === 0)}
        >
          {confirming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          {confirmLabel}
        </Button>
      </div>
    </div>
  )
}
