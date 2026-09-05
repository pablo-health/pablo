// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChargeHistory
 *
 * Every attempt to charge this client, newest first — declines included. A
 * failed row is not noise: it is the record that somebody tried, and the
 * reason is what tells the practice whether to ask for another card or simply
 * try again.
 */

"use client"

import { Skeleton } from "@/components/ui/skeleton"
import { formatCents } from "@/lib/money"
import { isPaymentsUnconfigured } from "@/lib/api/payments"
import { chargeStatusBadge, declineReason, formatChargeDate } from "@/lib/paymentDisplay"
import { usePatientCharges } from "@/hooks/usePayments"

interface ChargeHistoryProps {
  patientId: string
}

export function ChargeHistory({ patientId }: ChargeHistoryProps) {
  const { data: charges, isLoading, error } = usePatientCharges(patientId)

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  // The empty-card section above already says card payments are not set up;
  // repeating it under a second heading would be noise.
  if (isPaymentsUnconfigured(error)) return null

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load charges."}
      </p>
    )
  }

  if (!charges || charges.length === 0) {
    return <p className="text-sm text-neutral-500">No charges yet.</p>
  }

  return (
    <ul className="space-y-2">
      {charges.map((charge) => {
        const badge = chargeStatusBadge(charge.status)
        return (
          <li
            key={charge.id}
            className="rounded-lg border border-neutral-100 px-3 py-2.5"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm font-medium text-neutral-900">
                {formatCents(charge.amount_cents, charge.currency)}
              </span>
              <span className="flex shrink-0 items-center gap-3">
                <span className="text-xs text-neutral-500">
                  {formatChargeDate(charge.created_at)}
                </span>
                <span
                  className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${badge.className}`}
                >
                  {badge.label}
                </span>
              </span>
            </div>
            {charge.status === "failed" && (
              <p className="mt-1 text-xs text-red-600">{declineReason(charge)}</p>
            )}
          </li>
        )
      })}
    </ul>
  )
}
