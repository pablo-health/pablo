// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient's refill requests, newest first, each with where it stands
 * and what happens next.
 *
 * Rendered in the order the route returns them. The status label comes
 * straight from the stored status; nothing here infers a decision the
 * practice has not recorded.
 *
 * The date is the date of the status: when it was sent while it waits,
 * when it was decided once it has been. A decided row without a recorded
 * decision time falls back to the sent date rather than showing none.
 *
 * A decline is a clinical decision, not an error, so its pill is neutral
 * rather than red.
 */

"use client"

import type { RefillRequest, RefillRequestStatus } from "@/lib/api/patientRefills"
import { cn } from "@/lib/utils"
import { LIST_EMPTY, LIST_HEADING, NEXT_STEPS, STATUS_LABELS } from "./refillsCopy"

export interface RefillRequestListProps {
  requests: RefillRequest[]
  /** The request just sent from this page, marked out in the list. */
  highlightId?: string | null
  /** Whether the portal offers messaging; the declined line points there. */
  messagingEnabled?: boolean
  /** Rendered above the rows, under the heading. */
  notice?: React.ReactNode
}

const PILL_TONES: Record<RefillRequestStatus, string> = {
  requested: "bg-neutral-100 text-neutral-700",
  approved: "bg-green-100 text-green-800",
  needs_visit: "bg-amber-100 text-amber-800",
  declined: "bg-neutral-100 text-neutral-700",
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

function statusDate(request: RefillRequest): string {
  if (request.status === "requested") return request.created_at
  return request.decided_at ?? request.created_at
}

function nextStep(request: RefillRequest, messagingEnabled: boolean): string | null {
  if (request.status === "declined" && !messagingEnabled) return null
  return NEXT_STEPS[request.status]
}

export function RefillRequestList({
  requests,
  highlightId = null,
  messagingEnabled = false,
  notice,
}: RefillRequestListProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="portal-refills-list">
      <h3 className="text-sm font-semibold text-neutral-900">{LIST_HEADING}</h3>
      {notice}
      {requests.length === 0 ? (
        <p data-testid="portal-refills-list-empty" className="text-sm text-neutral-600">
          {LIST_EMPTY}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {requests.map((request) => {
            const highlighted = request.id === highlightId
            const date = statusDate(request)
            const step = nextStep(request, messagingEnabled)
            return (
              <li
                key={request.id}
                data-testid={`portal-refills-request-${request.id}`}
                data-highlighted={highlighted ? "true" : undefined}
                className={cn(
                  "flex flex-col gap-1 rounded-md border bg-white p-3",
                  highlighted ? "border-neutral-900 ring-1 ring-neutral-900" : "border-neutral-200",
                )}
              >
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 break-words text-sm font-medium text-neutral-900">
                    {request.medication_text}
                  </span>
                  <span
                    data-testid={`portal-refills-status-${request.id}`}
                    className={cn(
                      "rounded-full px-2 py-0.5 text-xs font-medium",
                      PILL_TONES[request.status],
                    )}
                  >
                    {STATUS_LABELS[request.status]}
                  </span>
                </span>
                <time
                  dateTime={date}
                  data-testid={`portal-refills-date-${request.id}`}
                  className="text-xs text-neutral-500"
                >
                  {formatDate(date)}
                </time>
                {step && (
                  <span
                    data-testid={`portal-refills-next-${request.id}`}
                    className="text-sm text-neutral-700"
                  >
                    {step}
                  </span>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
