// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The unbilled-sessions queue — Billing's main content.
 *
 * Every row is a finalized session with no succeeded charge, newest first.
 * "Charge card" links to the session, where the charge action already lives
 * (see `ChargeCardSection`). When the client has coverage on file the row
 * also offers "File claim", which opens the review step; a row whose claim
 * is already on its way shows where it stands instead.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { CircleDollarSign } from "lucide-react"
import { useUnbilledQueue } from "@/hooks/useBilling"
import { useUserTimeZone, formatInUserTimeZone } from "@/hooks/usePreferences"
import { formatCents } from "@/lib/money"
import type { UnbilledSessionItem } from "@/types/billing"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ClaimStateBadge } from "./claims/ClaimBadges"
import { ClaimReviewDialog } from "./claims/ClaimReviewDialog"

export function UnbilledQueue() {
  const { data, isLoading } = useUnbilledQueue()
  const timeZone = useUserTimeZone()

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }

  const items = data?.items ?? []

  if (items.length === 0) {
    return (
      <div className="card text-center py-12">
        <CircleDollarSign className="mx-auto h-8 w-8 text-neutral-300" />
        <p className="mt-3 text-sm font-medium text-neutral-900">Nothing unbilled</p>
        <p className="mt-1 text-sm text-neutral-500">
          Every finalized session has a successful charge on it.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <p className="text-sm text-neutral-500 mb-4">
        Amounts shown are what was charged, resolved from each client&rsquo;s rate. Stripe is the
        source of truth for money — fees, payouts and net are not shown here and will legitimately
        differ from these figures.
      </p>
      <ul className="space-y-1">
        {items.map((item) => (
          <QueueRow key={item.session_id} item={item} timeZone={timeZone} />
        ))}
      </ul>
    </div>
  )
}

/** A claim can be filed when the client is covered, the session was booked, and no claim is on its way. */
function offersClaim(item: UnbilledSessionItem): boolean {
  if (!item.has_coverage || item.appointment_id === null) return false
  return item.claim === null || item.claim.frequency_code === "8"
}

function QueueRow({ item, timeZone }: { item: UnbilledSessionItem; timeZone: string }) {
  const [reviewing, setReviewing] = useState(false)
  const claim = item.claim
  const draft = claim !== null && claim.state === "draft"
  const filed = claim !== null && claim.frequency_code !== "8" && !draft

  return (
    <li
      data-testid="unbilled-row"
      className="flex flex-wrap items-center justify-between gap-3 rounded-md px-3 py-3 -mx-3 hover:bg-neutral-50 transition-colors"
    >
      <Link href={`/dashboard/sessions/${item.session_id}`} className="flex min-w-0 items-center gap-3">
        <span className="truncate text-sm font-medium text-neutral-900">{item.patient_name}</span>
        <span className="shrink-0 text-xs text-neutral-500">
          {formatInUserTimeZone(item.session_date, timeZone, {
            month: "short",
            day: "numeric",
            year: "numeric",
          })}
        </span>
      </Link>

      <div className="flex shrink-0 items-center gap-2">
        <span className="text-sm font-medium text-neutral-900">
          {item.amount_cents !== null ? formatCents(item.amount_cents, item.currency) : "No rate set"}
        </span>
        {filed && claim && (
          <Link href={`/dashboard/billing/claims/${claim.id}`} data-testid="queue-claim-link">
            <ClaimStateBadge state={claim.state} />
          </Link>
        )}
        <Button asChild variant="outline" size="sm">
          <Link href={`/dashboard/sessions/${item.session_id}`}>Charge card</Link>
        </Button>
        {(offersClaim(item) || draft) && item.appointment_id !== null && (
          <>
            <Button size="sm" data-testid="file-claim" onClick={() => setReviewing(true)}>
              {draft ? "Review and file" : "File claim"}
            </Button>
            <ClaimReviewDialog
              open={reviewing}
              onOpenChange={setReviewing}
              appointmentId={item.appointment_id}
              patientName={item.patient_name}
              claimId={draft && claim ? claim.id : undefined}
            />
          </>
        )}
      </div>
    </li>
  )
}
