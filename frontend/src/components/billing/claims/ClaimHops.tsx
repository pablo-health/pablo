// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The claim's way to the payer, read off the receipt ledger the pipeline
 * keeps: every hop it took and every alert raised about it, oldest first.
 *
 * A receipt that changed the claim's state is a hop. One that left it where
 * it was — an acknowledgement, a status check that found nothing, a deadline
 * alert — moved nothing and reads as a note beside the hops rather than as a
 * step of its own. The ledger is shown, not the derived path, because it is
 * the ledger that keeps the vendor's identifiers and the moment of each.
 */

"use client"

import { Check, Circle } from "lucide-react"
import type { ClaimReceipt, ClaimReceiptKind } from "@/types/claims"

const RECEIPT_LABELS: Record<ClaimReceiptKind, string> = {
  submitted: "Taken by the clearinghouse",
  ch_accepted: "Accepted by the clearinghouse",
  payer_accepted: "Accepted by the payer",
  rejected: "Rejected",
  stalled: "No receipt in time",
  acknowledged: "Acknowledged",
  status_checked: "Status checked",
  deadline_approaching: "Deadline approaching",
  deadline_missed: "Deadline passed",
}

/** A receipt that left the claim where it was moved nothing. */
function movedTheClaim(receipt: ClaimReceipt): boolean {
  return receipt.to_state !== receipt.from_state
}

function formatMoment(occurredAt: string): string {
  return new Date(occurredAt).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

/** The `system:code` pairs a receipt's detail carries, if any. */
function detailCodes(receipt: ClaimReceipt): string[] {
  const codes = receipt.detail.codes
  if (!Array.isArray(codes)) return []
  return codes.map((entry) => {
    const { system, code } = entry as { system?: string; code?: string }
    return system ? `${system}:${code}` : `${code}`
  })
}

export function ClaimHops({ receipts }: { receipts: ClaimReceipt[] }) {
  const ordered = [...receipts].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at))

  if (ordered.length === 0) {
    return <p className="text-sm text-neutral-500">The claim has not left the practice yet.</p>
  }

  return (
    <ol className="space-y-2" data-testid="claim-hops">
      {ordered.map((receipt) => {
        const hop = movedTheClaim(receipt)
        const codes = detailCodes(receipt)
        return (
          <li
            key={receipt.id}
            data-testid={hop ? "claim-hop" : "claim-note"}
            data-kind={receipt.kind}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
          >
            {hop ? (
              <Check className="h-4 w-4 text-emerald-700" aria-label="Moved" />
            ) : (
              <Circle className="h-3 w-3 text-neutral-300" aria-label="Noted" />
            )}
            <span className={hop ? "text-neutral-900" : "text-neutral-500"}>
              {RECEIPT_LABELS[receipt.kind]}
            </span>
            <span className="text-xs text-neutral-500">{formatMoment(receipt.occurred_at)}</span>
            {codes.length > 0 && (
              <span className="font-mono text-xs text-neutral-500">{codes.join(", ")}</span>
            )}
            {(receipt.vendor_transaction_id ?? receipt.vendor_event_id) && (
              <span className="font-mono text-xs text-neutral-400">
                {receipt.vendor_transaction_id ?? receipt.vendor_event_id}
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}
