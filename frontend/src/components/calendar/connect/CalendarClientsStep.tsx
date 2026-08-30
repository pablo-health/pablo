// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Fragment, useSyncExternalStore } from "react"
import { Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { SetupStepHead } from "@/components/setup"
import type { BusyWindowsGranted, BusyWindowsNotGranted, ImportProposal } from "@/lib/api/scheduling"
import { busyWindowsGranted } from "@/lib/api/scheduling"
import { GRID_HOURS, GRID_WEEKDAYS, busyCellKeys, cellKey, seriesCellKeys } from "./weekGrid"

/** Stagger between one qualifying cell lifting and the next, in ms. */
const STAGGER_MS = 32
/** How long the lift/settle transition itself takes. */
const TRANSITION_MS = 420

const DAY_LABELS: Record<(typeof GRID_WEEKDAYS)[number], string> = {
  0: "M",
  1: "T",
  2: "W",
  3: "Th",
  4: "F",
}

function hourLabel(hour: number): string {
  if (hour === 12) return "12"
  return hour > 12 ? String(hour - 12) : String(hour)
}

// Reads a client-only browser feature the same way OnboardingPasskeyForm
// reads WebAuthn support: false on the server, the real value once mounted,
// no setState-in-effect and no hydration mismatch.
function subscribe(): () => void {
  return () => {}
}
function reducedMotionSnapshot(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches
}
function reducedMotionServerSnapshot(): boolean {
  return false
}

function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, reducedMotionSnapshot, reducedMotionServerSnapshot)
}

interface CalendarClientsStepProps {
  busyWindows: BusyWindowsGranted | BusyWindowsNotGranted | undefined
  proposal: ImportProposal | null
  scanning: boolean
  error: string | null
  onScan: () => void
  onSkip: () => void
}

export function CalendarClientsStep({
  busyWindows,
  proposal,
  scanning,
  error,
  onScan,
  onSkip,
}: CalendarClientsStepProps) {
  const reducedMotion = usePrefersReducedMotion()
  const scanned = proposal !== null

  const busyKeys =
    busyWindows && busyWindowsGranted(busyWindows) ? busyCellKeys(busyWindows.windows) : null
  const matchedKeys = scanned ? seriesCellKeys(proposal.series) : new Set<string>()

  // Pre-scan: only the calendar's own busy shape, undifferentiated — Pablo
  // can't yet say which of these look like sessions. Post-scan: everything
  // a series matched, plus (when BUSY was granted) whatever else the
  // calendar showed as busy, now sorted into the two end states.
  const shownKeys = scanned ? new Set([...(busyKeys ?? []), ...matchedKeys]) : (busyKeys ?? null)

  const qualifyingCount = scanned
    ? [...shownKeys!].filter((key) => matchedKeys.has(key)).length
    : 0
  const ghostCount = scanned ? shownKeys!.size - qualifyingCount : 0

  let sageIndex = 0

  return (
    <div className="space-y-4">
      <SetupStepHead
        eyebrow="Step 3 · one-time look · optional"
        title="Bring over your week"
        lede="Pablo looks at the rhythm of your calendar - events that repeat weekly or every other week, the way sessions do. It can't tell a client from a standing meeting, so nothing is added until you say so."
      />

      <div className="rounded-xl border border-border bg-card p-3.5 pb-3">
        {shownKeys === null ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            {scanning ? "Reading your calendar…" : "Your week will show here once you look."}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <div
              data-testid="week-grid"
              className="grid min-w-[340px] gap-[3px]"
              style={{ gridTemplateColumns: "34px repeat(5, minmax(58px, 1fr))" }}
            >
              <div />
              {GRID_WEEKDAYS.map((day) => (
                <div
                  key={day}
                  className="flex items-center justify-center text-[10.5px] font-bold uppercase tracking-wide text-muted-foreground"
                >
                  {DAY_LABELS[day]}
                </div>
              ))}
              {GRID_HOURS.map((hour) => (
                <Fragment key={hour}>
                  <div className="flex items-center justify-end pr-1 text-[10.5px] font-bold text-muted-foreground opacity-80">
                    {hourLabel(hour)}
                  </div>
                  {GRID_WEEKDAYS.map((day) => {
                    const key = cellKey(day, hour)
                    if (!shownKeys.has(key)) return <div key={key} />
                    if (!scanned) {
                      return (
                        <div
                          key={key}
                          className="h-[22px] rounded-[5px] border border-border bg-muted"
                        />
                      )
                    }
                    const qualifies = matchedKeys.has(key)
                    const delayMs = reducedMotion
                      ? 0
                      : qualifies
                        ? sageIndex++ * STAGGER_MS
                        : qualifyingCount * STAGGER_MS
                    return (
                      <div
                        key={key}
                        className={
                          qualifies
                            ? "h-[22px] -translate-y-px rounded-[5px] border border-secondary-500 bg-secondary-500"
                            : "h-[22px] rounded-[5px] border border-dashed border-border bg-transparent opacity-[0.55]"
                        }
                        style={{
                          transitionProperty: "background-color, border-color, opacity, transform",
                          transitionDuration: reducedMotion ? "0ms" : `${TRANSITION_MS}ms`,
                          transitionTimingFunction: "cubic-bezier(.2,.7,.3,1)",
                          transitionDelay: `${delayMs}ms`,
                        }}
                      />
                    )
                  })}
                </Fragment>
              ))}
            </div>
          </div>
        )}

        <div className="mt-3 flex flex-wrap gap-4 border-t border-border pt-2.5 text-xs text-muted-foreground">
          {scanned ? (
            <>
              <span className="inline-flex items-center gap-2">
                <span className="h-3.5 w-3.5 rounded border border-secondary-500 bg-secondary-500" />
                <b className="font-bold text-neutral-900" data-testid="qualifying-count">
                  {qualifyingCount}
                </b>
                {qualifyingCount === 1 ? "repeating slot" : "repeating slots"} - these look like
                sessions
              </span>
              <span className="inline-flex items-center gap-2">
                <span className="h-3.5 w-3.5 rounded border border-dashed border-border" />
                <b className="font-bold text-neutral-900" data-testid="ghost-count">
                  {ghostCount}
                </b>
                {ghostCount === 1 ? "other" : "others"} - left as they are
              </span>
            </>
          ) : shownKeys ? (
            <span>A typical week from your calendar, as Pablo sees it before it sorts anything.</span>
          ) : null}
        </div>
      </div>

      {scanned ? (
        <div className="flex items-start gap-2 rounded-lg bg-card p-2.5 text-xs text-muted-foreground">
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-secondary-600" />
          <span>
            Found <b className="font-bold text-neutral-900">{qualifyingCount}</b> slot
            {qualifyingCount === 1 ? "" : "s"} that repeat like sessions, over the last{" "}
            {proposal.lookback_days} days and the next {proposal.horizon_days}.{" "}
            <b className="font-bold text-neutral-900" data-testid="left-alone-count">
              {proposal.left_alone}
            </b>{" "}
            other calendar event{proposal.left_alone === 1 ? "" : "s"} didn&rsquo;t fit that
            pattern. Next: you decide which ones are clients.
          </span>
        </div>
      ) : (
        <div className="flex items-center gap-2 border-t border-border pt-4">
          <Button variant="ghost" size="sm" onClick={onSkip} disabled={scanning}>
            Skip, I&rsquo;ll add them myself
          </Button>
          <span className="flex-1" />
          <Button onClick={onScan} disabled={scanning}>
            {scanning ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {scanning ? "Reading your calendar…" : "Look at my week"}
          </Button>
        </div>
      )}

      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
