// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The unbilled-sessions queue — Billing's main content.
 *
 * Every row is a finalized session with no succeeded charge, newest first.
 * Nothing here charges anything: a row links to its session, where the
 * charge action already lives (see `ChargeCardSection`). This is the way
 * back to that action for a clinician who skipped it at signing time.
 */

"use client"

import Link from "next/link"
import { CircleDollarSign } from "lucide-react"
import { useUnbilledQueue } from "@/hooks/useBilling"
import { useUserTimeZone, formatInUserTimeZone } from "@/hooks/usePreferences"
import { formatCents } from "@/lib/money"
import { Skeleton } from "@/components/ui/skeleton"

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
          <li key={item.session_id}>
            <Link
              href={`/dashboard/sessions/${item.session_id}`}
              className="flex items-center justify-between rounded-md px-3 py-3 -mx-3 hover:bg-neutral-50 transition-colors"
            >
              <span className="flex items-center gap-3 min-w-0">
                <span className="truncate text-sm font-medium text-neutral-900">
                  {item.patient_name}
                </span>
                <span className="shrink-0 text-xs text-neutral-500">
                  {formatInUserTimeZone(item.session_date, timeZone, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </span>
              </span>
              <span className="shrink-0 text-sm font-medium text-neutral-900">
                {item.amount_cents !== null
                  ? formatCents(item.amount_cents, item.currency)
                  : "No rate set"}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
