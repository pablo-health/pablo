// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { usePanelApplications } from "@/hooks/useCredentialingChecklist"
import { usePreferences } from "@/hooks/usePreferences"
import { RequestReceived } from "./RequestReceived"
import type {
  PanelApplication,
  PanelApplicationStatus,
} from "@/types/credentialing"

/**
 * Where her panel applications stand, and the short list of things we need
 * from her.
 *
 * The screen leads with what she owes and puts everything we are carrying
 * underneath, visible but quiet. That split is the whole product promise made
 * legible: she is paying us to run this, so a board that nagged her about all
 * of it would be the process she was trying to stop carrying, reprinted.
 *
 * Nothing here is editable. Pablo moves these rows, and a clinician who could
 * set her own application to "effective" would be recording a fact she is not
 * the source of.
 */
export function PanelApplications() {
  const { data, isLoading, isError } = usePanelApplications()
  // Whether she has ASKED, which is a different fact from whether anything has
  // been filed and is the only way to tell an empty board apart from a board
  // belonging to someone who never wanted one. The wizard writes it; the
  // operator queue reads it; until now nothing showed it back to her.
  const { data: preferences } = usePreferences()

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }

  // A failure has to say so rather than render as "nothing to do" — the two
  // look identical and only one of them means she can stop thinking about it.
  if (isError || !data) {
    return (
      <p className="text-sm text-muted-foreground">
        We couldn&rsquo;t load your applications just now. Nothing is lost; try
        again in a moment.
      </p>
    )
  }

  // Nothing filed yet, and what to show depends on whether she asked for any
  // of this. A clinician who has not begun is still told nothing — saying "no
  // applications" to someone who never wanted one is telling her what she just
  // did. One who HAS asked gets the acknowledgement, because for her the empty
  // board is not "nothing to see", it is days of silence after a request.
  if (data.data.length === 0) {
    return preferences?.billing_setup_wants_credentialing ? <RequestReceived /> : null
  }

  // The API has already put hers first; this only splits the list it returned,
  // and never reorders within either half.
  const needsHer = (app: PanelApplication) =>
    app.action_owner === "therapist" && !isSettled(app.status)
  const mine = data.data.filter(needsHer)
  const ours = data.data.filter((app) => !needsHer(app))

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <h3 className="font-display text-lg font-semibold text-neutral-900">
          {data.needs_you === 0
            ? "Nothing needs you right now"
            : `${data.needs_you} ${data.needs_you === 1 ? "thing needs" : "things need"} you`}
        </h3>
        {mine.length === 0 ? (
          <p className="text-sm text-neutral-600">
            We&rsquo;ll come to you when a payer asks for something only you can
            give &mdash; a signature, a document, a date. Until then there is
            nothing here for you to do.
          </p>
        ) : (
          <ul className="space-y-3">
            {mine.map((app) => (
              <ApplicationRow key={app.id} application={app} mine />
            ))}
          </ul>
        )}
      </section>

      {ours.length > 0 && (
        <section className="space-y-3">
          <h3 className="font-display text-lg font-semibold text-neutral-900">
            What Pablo is carrying
          </h3>
          <ul className="space-y-3">
            {ours.map((app) => (
              <ApplicationRow key={app.id} application={app} mine={false} />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

interface ApplicationRowProps {
  application: PanelApplication
  mine: boolean
}

function ApplicationRow({ application, mine }: ApplicationRowProps) {
  return (
    <li
      className={`rounded-lg border p-4 ${
        mine ? "border-honey-300 bg-honey-50/50" : "border-border"
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="font-medium text-neutral-900">
          {application.payer_name}
        </span>
        <span className="text-sm text-muted-foreground">
          {STATUS_LABELS[application.status]}
        </span>
      </div>

      {/* Her own words back to her, when it is hers. `awaiting` is written for
          her by whoever is running the application, so it beats anything a
          status label could be expanded into. */}
      {mine && application.awaiting && (
        <p className="mt-2 text-sm text-neutral-700">{application.awaiting}</p>
      )}

      <p className="mt-2 text-xs text-muted-foreground">
        {waitingLine(application)}
      </p>
    </li>
  )
}

/**
 * The line that answers the question she actually has, which is whether the
 * silence means something is wrong.
 *
 * A deadline outranks everything else on the row: an unanswered payer request
 * kills an application outright when its window closes, and that is the one
 * fact worth putting above how long she has waited.
 */
function waitingLine(application: PanelApplication): string {
  if (application.due_at) {
    return `Needed by ${formatDate(application.due_at)}`
  }
  if (application.effective_at) {
    return `Effective ${formatDate(application.effective_at)}`
  }
  if (application.days_since_submitted === null) {
    return "Not submitted yet"
  }
  if (application.days_since_submitted < 1) {
    return "Submitted today"
  }
  const weeks = Math.floor(application.days_since_submitted / 7)
  // Under a fortnight, days are what she is counting; past that, weeks are —
  // "submitted 63 days ago" makes her do arithmetic to feel how long it is.
  if (weeks < 2) {
    return `Submitted ${application.days_since_submitted} ${
      application.days_since_submitted === 1 ? "day" : "days"
    } ago`
  }
  return `Submitted ${weeks} weeks ago`
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  })
}

//: Written for her rather than for us. "in_review" is the payer's word and
//: says nothing; "With the payer" says who has it.
const STATUS_LABELS: Record<PanelApplicationStatus, string> = {
  researching: "Working out what they need",
  caqh_ready: "Ready to file",
  submitted: "Filed",
  in_review: "With the payer",
  info_requested: "They’ve asked for something",
  contract_received: "Contract received",
  effective: "In network",
  closed_panel_appeal: "Panel closed — appealing",
  denied: "Declined",
  recredentialing: "Recredentialing",
}

const SETTLED: ReadonlySet<PanelApplicationStatus> = new Set([
  "effective",
  "denied",
])

function isSettled(status: PanelApplicationStatus): boolean {
  return SETTLED.has(status)
}
