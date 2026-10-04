// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { InfoPopover } from "@/components/ui/InfoPopover"
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
 * reaches, so this copy cannot promise a limit that isn't real. The guarantee
 * sits behind "About this permission": it is how far the access reaches, not a
 * condition of choosing it. */
const WRITE_TARGET_COPY: Record<CalendarWriteTarget, { label: string; does: string }> = {
  app_calendar: {
    label: "A separate Pablo calendar",
    does: "Keeps Pablo sessions separate from your main calendar.",
  },
  primary: {
    label: "My main calendar",
    does: "Adds Pablo sessions to the calendar you already use.",
  },
}

/** What the busy grant does, and nothing more: busy time is left out of every
 * time Pablo offers (the slot picker, booking links, the client portal), and a
 * time the therapist picks by hand still books. That warning shows in its own
 * dialog when it applies, so it is not described here. Your main calendar and
 * the one Pablo follows both count. */
const BUSY_COPY = {
  label: "Check for scheduling conflicts",
  does: "Pablo won't offer times your calendar shows as busy.",
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
    does: "Shows no identifying details.",
    preview: "Therapy Session",
  },
  initials: {
    label: "Initials",
    does: "Easy to recognize without showing a full name.",
    preview: "J.M.",
    recommended: true,
  },
  full: {
    label: "Full name",
    does:
      "Use only with a Google Workspace account covered by your practice's agreement with Google.",
    preview: "Jane Miller",
  },
}

const TITLING_ORDER: EventTitling[] = ["generic", "initials", "full"]

interface CalendarSessionsStepProps {
  /** Where this step sits in the wizard's stepper, so the card and the
   * stepper always give the same number. */
  step: number
  status: GoogleCalendarStatus | undefined
  options: GoogleCalendarConsentOptions | undefined
  selection: GoogleCalendarSelection
  onSelectionChange: (selection: GoogleCalendarSelection) => void
  connecting: boolean
  error: string | null
  /** Go to Google: to connect, or to change what an existing connection holds. */
  onConnect: () => void
  /** Save how events read on an existing connection. Pablo's own record, so
   * no trip to Google. */
  onSaveTitling: () => void
  /** True once the therapist has confirmed the account is covered. Only
   * meaningful for the full-name choice, which is gated on it. */
  attested: boolean
  onAttestedChange: (attested: boolean) => void
}

export function CalendarSessionsStep({
  step,
  status,
  options,
  selection,
  onSelectionChange,
  connecting,
  error,
  onConnect,
  onSaveTitling,
  attested,
  onAttestedChange,
}: CalendarSessionsStepProps) {
  const promiseFor = (id: string) =>
    options?.write_targets.find((option) => option.id === id)?.promise

  const connected = status?.connected === true
  // Where sessions go and busy times are Google's permissions; changing
  // either on a live connection means Google asks again. How events read is
  // Pablo's own record and saves without leaving the page. An older backend
  // that does not report the busy grant is treated as unchanged there.
  const googleChanged =
    connected &&
    (selection.write_target !== status?.write_target ||
      (typeof status?.busy === "boolean" && selection.busy !== status.busy))
  const titlingChanged = connected && selection.event_titling !== status?.event_titling
  const attestationStatement = status?.titling_attestation_statement

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow={`Step ${step}`}
        title="Choose a calendar"
        lede="Choose where sessions booked in Pablo should appear."
      />

      <fieldset className="space-y-3">
        <legend className="sr-only">Where sessions booked in Pablo appear</legend>
        {(Object.keys(WRITE_TARGET_COPY) as CalendarWriteTarget[]).map((target) => {
          const copy = WRITE_TARGET_COPY[target]
          const promise = promiseFor(target)
          return (
            // The info button sits beside the label, not in it, so opening
            // it never picks the option.
            <div
              key={target}
              className="flex items-start gap-2 rounded-lg border border-border p-4 hover:bg-muted/40"
            >
              <label className="flex flex-1 cursor-pointer gap-3">
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
                </span>
              </label>
              {promise ? <InfoPopover label="About this permission">{promise}</InfoPopover> : null}
            </div>
          )
        })}
      </fieldset>

      <div className="flex items-start gap-2 rounded-lg border border-border p-4 hover:bg-muted/40">
        <label className="flex flex-1 cursor-pointer gap-3">
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
          </span>
        </label>
        {options?.busy.promise ? (
          <InfoPopover label="About this permission">{options.busy.promise}</InfoPopover>
        ) : null}
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-medium text-neutral-900">How should sessions appear?</legend>
        <p className="text-sm text-muted-foreground">
          This title may appear in notifications and shared calendars.
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

        {status?.titling_needs_attestation ? (
          <p className="rounded-lg border border-amber-300 bg-amber-50/60 p-3 text-sm text-neutral-900">
            You chose full names for a different Google account. Events are reading as initials
            until you confirm this account is covered too.
          </p>
        ) : null}

        {selection.event_titling === "full" && attestationStatement ? (
          // The wording comes from the API, which records this exact text
          // with the confirmation; writing it here as well is how the two
          // drifted apart once. Without it (an older backend) there is
          // nothing to confirm, so the confirmation is not offered.
          //
          // No line about personal Gmail accounts: the connection does not
          // reliably know the account's address (a separate Pablo calendar
          // is identified by an opaque id), so nothing could enforce it.
          <div className="rounded-lg border border-amber-300 bg-amber-50/60 p-4">
            <label className="flex cursor-pointer gap-3">
              <Checkbox
                className="mt-1"
                checked={attested}
                onCheckedChange={(checked) => onAttestedChange(checked === true)}
              />
              <span className="text-sm text-neutral-900">{attestationStatement}</span>
            </label>
          </div>
        ) : null}
      </fieldset>

      {!connected ? (
        <Button onClick={onConnect} disabled={connecting}>
          {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          Continue with Google
        </Button>
      ) : googleChanged ? (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Google will ask you to approve the updated access.
          </p>
          <Button onClick={onConnect} disabled={connecting}>
            {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
            Continue with Google
          </Button>
        </div>
      ) : titlingChanged ? (
        <Button onClick={onSaveTitling} disabled={connecting}>
          {connecting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          Save event titles
        </Button>
      ) : null}

      {error ? <p className="text-sm text-red-600">{error}</p> : null}
    </div>
  )
}
