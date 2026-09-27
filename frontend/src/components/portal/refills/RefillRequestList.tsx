// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient's refill requests, newest first, each with where it stands.
 *
 * Rendered in the order the route returns them. The status label comes
 * straight from the stored status; nothing here infers a decision the
 * practice has not recorded.
 */

"use client"

import type { RefillRequest } from "@/lib/api/patientRefills"
import { LIST_EMPTY, LIST_HEADING, STATUS_LABELS } from "./refillsCopy"

export interface RefillRequestListProps {
  requests: RefillRequest[]
}

function formatSubmitted(value: string): string {
  return new Date(value).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  })
}

export function RefillRequestList({ requests }: RefillRequestListProps) {
  return (
    <div className="flex flex-col gap-2" data-testid="portal-refills-list">
      <h3 className="text-sm font-semibold text-neutral-900">{LIST_HEADING}</h3>
      {requests.length === 0 ? (
        <p data-testid="portal-refills-list-empty" className="text-sm text-neutral-600">
          {LIST_EMPTY}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {requests.map((request) => (
            <li
              key={request.id}
              data-testid={`portal-refills-request-${request.id}`}
              className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-200 bg-white p-3"
            >
              <span className="flex min-w-0 flex-col">
                <span className="break-words text-sm font-medium text-neutral-900">
                  {request.medication_text}
                </span>
                <span className="text-xs text-neutral-500">
                  {formatSubmitted(request.created_at)}
                </span>
              </span>
              <span
                data-testid={`portal-refills-status-${request.id}`}
                className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium text-neutral-700"
              >
                {STATUS_LABELS[request.status]}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
