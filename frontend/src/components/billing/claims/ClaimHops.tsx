// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The claim's path to the payer as a timeline: each hop it has reached,
 * with the receipt's timestamp where one is on file.
 */

"use client"

import { Check, Circle } from "lucide-react"
import type { ClaimHop, ClaimHopKind } from "@/types/claims"

const HOP_LABELS: Record<ClaimHopKind, string> = {
  built: "Built from the session",
  submitted: "Taken by the clearinghouse",
  clearinghouse_accepted: "Accepted by the clearinghouse",
  payer_accepted: "Accepted by the payer",
  adjudicated: "Adjudicated",
}

export function ClaimHops({ hops }: { hops: ClaimHop[] }) {
  return (
    <ol className="space-y-2" data-testid="claim-hops">
      {hops.map((hop) => (
        <li
          key={hop.kind}
          data-testid="claim-hop"
          data-kind={hop.kind}
          data-reached={hop.reached}
          className="flex items-center gap-3 text-sm"
        >
          {hop.reached ? (
            <Check className="h-4 w-4 text-emerald-700" aria-label="Reached" />
          ) : (
            <Circle className="h-4 w-4 text-neutral-300" aria-label="Not yet" />
          )}
          <span className={hop.reached ? "text-neutral-900" : "text-neutral-500"}>
            {HOP_LABELS[hop.kind]}
          </span>
          {hop.at && (
            <span className="text-xs text-neutral-500">
              {new Date(hop.at).toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </span>
          )}
        </li>
      ))}
    </ol>
  )
}
