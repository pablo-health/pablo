// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { SetupStepHead } from "@/components/setup"
import type {
  CalendarWriteTarget,
  EventTitling,
  GoogleCalendarConsentOptions,
  GoogleCalendarSelection,
  GoogleCalendarStatus,
} from "@/lib/api/scheduling"

/** What each choice does, in the therapist's terms. The guarantee that goes
 * with it is never written here — it comes back from the API, generated from
 * the provider's own declaration of how far the underlying permission
 * reaches, so this copy cannot promise a limit that isn't real. */
const WRITE_TARGET_COPY: Record<CalendarWriteTarget, { label: string; does: string }> = {
  app_calendar: {
    label: "A calendar Pablo makes",
    does: "Pablo adds a calendar to your Google account and puts your sessions on it.",
  },
  primary: {
    label: "My main calendar",
    does: "Your sessions go onto the calendar you already use.",
  },
}

const BUSY_COPY = {
  label: "Also check when I'm busy",
  does: "Pablo looks at your calendar before offering a time, so you aren't double-booked.",
}

/** The three rungs, each with what an event actually ends up saying.
 *
 * The preview is the point: "initials" means nothing until you see "J.M."
 * sitting where the event title goes. */
const TITLING_COPY: Record<
  EventTitling,
  { label: string; does: string; preview: string; recommended?: boolean }
> = {
  generic: {
    label: "Therapy Session",
    does: "Nothing identifying leaves Pablo.",
    preview: "Therapy Session",
  },
  initials: {
    label: "Initials",
    does: "Enough for you to recognise. Nothing for anyone reading over your shoulder.",
    preview: "J.M.",
    recommended: true,
  },
  full: {
    label: "Full name",
    does: "Only if this Google account is covered by your practice's own agreement.",
    preview: "Jane Miller",
  },
}

const TITLING_ORDER: EventTitling[] = ["generic", "initials", "full"]

interface CalendarSessionsStepProps {
  status: GoogleCalendarStatus | undefined
  options: GoogleCalendarConsentOptions | undefined
  selection: GoogleCalendarSelection
  onSelectionChange: (selection: GoogleCalendarSelection) => void
  connecting: boolean
  error: string | null
  onConnect: () => void
  /** True once the therapist has confirmed the account is covered. Only
   * meaningful for the full-name choice, which is gated on it. */
  attested: boolean
  onAttestedChange: (attested: boolean) => void
}

export function CalendarSessionsStep({
  status,
  options,
  selection,
  onSelectionChange,
  connecting,
  error,
  onConnect,
  attested,
  onAttestedChange,
}: CalendarSessionsStepProps) {
  const promiseFor = (id: string) =>
    options?.write_targets.find((option) => option.id === id)?.promise

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 2"
        title="Where your sessions go"
        lede="Pick where Pablo writes your sessions. Google is only asked for what you pick here."
      />

      <fieldset className="space-y-3">
        <legend className="sr-only">Where Pablo writes your sessions</legend>
        {(Object.keys(WRITE_TARGET_COPY) as CalendarWriteTarget[]).map((target) => {
          const copy = WRITE_TARGET_COPY[target]
          const promise = promiseFor(target)
          return (
            <label
              key={target}
              className="flex cursor-pointer gap-3 rounded-lg border border-border p-4 hover:bg-muted/40"
            >
              <input
                type="radio"
                name="calendar-write-target"
                className="mt-1"
                checked={selection.write_target === target}
                onChange={() => onSelectionChange({ ...selection, write_target: target })}
              />
              <span className="space-y-1">
                <span className="flex items-center gap-2 text-sm font-medium text-neutral-900">
                  {copy.label}
                  {options?.default_write_target === target ? (
                    <span className="rounded-full bg-primary-100 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                      Recommended
                    </span>
                  ) : null}
                </span>
                <span className="block text-sm text-muted-foreground">{copy.does}</span>
                {promise ? (
                  <span className="block text-xs text-muted-foreground">{promise}</span>
                ) : null}
              </span>
            </label>
          )
        })}
      </fieldset>

      <label className="flex cursor-pointer gap-3 rounded-lg border border-border p-4 hover:bg-muted/40">
        <Checkbox
          className="mt-1"
          checked={selection.busy}
          onCheckedChange={(checked) =>
            onSelectionChange({ ...selection, busy: checked === true })
          }
          aria-label={BUSY_COPY.label}
        />
        <span className="space-y-1">
          <span className="block text-sm font-medium text-neutral-900">{BUSY_COPY.label}</span>
          <span className="block text-sm text-muted-foreground">{BUSY_COPY.does}</span>
          {options?.busy.promise ? (
            <span className="block text-xs text-muted-foreground">{options.busy.promise}</span>
          ) : null}
        </span>
      </label>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-neutral-900">
          How should those events read?
        </legend>
        <p className="text-sm text-muted-foreground">
          Whatever you pick is what shows on your phone&rsquo;s lock screen, and in any calendar you
          share.
        </p>
        {TITLING_ORDER.map((style) => {
          const copy = TITLING_COPY[style]
          return (
            <label
              key={style}
              className="flex cursor-pointer gap-3 rounded-lg border border-border p-4 hover:bg-muted/40"
            >
              <input
                type="radio"
                name="calendar-event-titling"
                className="mt-1"
                checked={selection.event_titling === style}
                onChange={() => onSelectionChange({ ...selection, event_titling: style })}
              />
              <span className="space-y-1">
                <span className="flex items-center gap-2 text-sm font-medium text-neutral-900">
                  {copy.label}
                  {copy.recommended ? (
                    <span className="rounded-full bg-primary-100 px-2 py-0.5 text-[11px] font-medium text-primary-700">
                      Recommended
                    </span>
                  ) : null}
                </span>
                <span className="block text-sm text-muted-foreground">{copy.does}</span>
                <span className="block text-xs text-muted-foreground">
                  {copy.preview} &middot; 3:00&ndash;3:50 PM
                </span>
              </span>
            </label>
          )
        })}

        {selection.event_titling === "full" ? (
          <label className="flex cursor-pointer gap-3 rounded-lg border border-amber-300 bg-amber-50/60 p-4">
            <Checkbox
              className="mt-1"
              checked={attested}
              onCheckedChange={(checked) => onAttestedChange(checked === true)}
              aria-label="Confirm this Google account is covered by your practice's agreement"
            />
            <span className="text-sm text-neutral-900">
              I confirm this Google account is covered by a business associate agreement my practice
              holds. <strong>Pablo&rsquo;s agreement does not cover your Google account</strong> — a
              personal Gmail address never qualifies.
            </span>
          </label>
        ) : null}
      </fieldset>

      <div className="space-y-2">
        {status?.connected ? (
          <p className="text-sm text-muted-foreground">
            Each of these is a separate permission, so changing one means asking Google again.
          </p>
        ) : null}
        <Button onClick={onConnect} disabled={connecting}>
          {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          {status?.connected ? "Ask Google again" : "Connect Google Calendar"}
        </Button>
      </div>

      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
