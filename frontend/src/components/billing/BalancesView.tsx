// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Balances — Billing's collections surface.
 *
 * The unbilled queue beside it asks "what have I not charged for yet". This
 * asks "who has not paid", which is a different question with a different
 * answer: a session charged and declined is off that queue and on this list.
 *
 * Oldest outstanding first, because the balance that has been sitting longest
 * is the one that needs the conversation — not the largest. Every row links
 * to the client's chart, where the ledger behind the figure and the actions
 * on it already live; nothing is charged from here, so no money moves from a
 * screen that shows one line per person.
 */

"use client"

import Link from "next/link"
import { Wallet } from "lucide-react"
import { useBalances } from "@/hooks/useBilling"
import { formatCents } from "@/lib/money"
import { formatChargeDate } from "@/lib/paymentDisplay"
import { Skeleton } from "@/components/ui/skeleton"
import type { ClientBalanceItem } from "@/types/payments"

export function BalancesView() {
  const { data, isLoading, error } = useBalances()

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load balances."}
      </p>
    )
  }

  const items = data?.items ?? []

  if (items.length === 0) {
    return (
      <div className="card text-center py-12">
        <Wallet className="mx-auto h-8 w-8 text-neutral-300" />
        <p className="mt-3 text-sm font-medium text-neutral-900">Nothing outstanding</p>
        <p className="mt-1 text-sm text-neutral-500">
          Every client&rsquo;s ledger nets to zero.
        </p>
      </div>
    )
  }

  return (
    <div className="card">
      <p className="mb-4 text-sm text-neutral-500">
        Clients whose ledger does not net to zero, oldest first. A credit is a refund the
        practice owes and is listed alongside the debts rather than hidden.
      </p>
      <ul className="space-y-1">
        {items.map((item) => (
          <BalanceRow key={item.patient_id} item={item} />
        ))}
      </ul>
    </div>
  )
}

function BalanceRow({ item }: { item: ClientBalanceItem }) {
  const credit = item.balance_cents < 0
  return (
    <li>
      <Link
        href={`/dashboard/patients/${item.patient_id}`}
        className="flex items-center justify-between gap-3 rounded-lg px-3 py-3 transition-colors hover:bg-primary-50/40"
      >
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-neutral-900">
            {item.patient_name}
          </span>
          <span className="text-xs text-neutral-500">
            Outstanding since {formatChargeDate(item.outstanding_since)}
          </span>
          {/* Said on the row rather than once at the top of the list: this
              list is read one line at a time, and the line being chased is
              the one that has to carry the caveat. */}
          {!item.outcome_known && (
            <span className="text-xs text-amber-700">
              At least this &mdash; your billing service receives this
              payer&rsquo;s remittances, not Pablo
            </span>
          )}
        </span>
        <span
          className={`shrink-0 text-sm font-medium ${
            credit ? "text-secondary-700" : "text-neutral-900"
          }`}
        >
          {credit
            ? `Credit ${formatCents(-item.balance_cents, item.currency)}`
            : formatCents(item.balance_cents, item.currency)}
        </span>
      </Link>
    </li>
  )
}
