// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Reports — the practice's financial report for an explicit window.
 *
 * Four sections read from one call to `GET /api/billing/report`: A/R aging
 * (a live snapshot as of the window's end, not filtered by it), payer mix,
 * collections rate, and claim-to-payment lag. There is no default window —
 * the caller picks one, because a report that silently means "this month"
 * answers a question nobody asked.
 *
 * Every percentage here is computed from the cents the API returns, never
 * received as one: the backend stays exact and this is the one place that
 * rounds a figure for a screen.
 */

"use client"

import { useState } from "react"
import { BarChart3 } from "lucide-react"
import { useBillingReport } from "@/hooks/useBilling"
import { formatCents } from "@/lib/money"
import type {
  AgingBucketResponse,
  BillingReportResponse,
  PayerLagResponse,
  PayerMixEntryResponse,
} from "@/types/billingReport"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function firstOfMonth(date: Date): string {
  return isoDate(new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1)))
}

function EmptySection({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-dashed border-neutral-200 py-8 text-center">
      <p className="text-sm text-neutral-500">{message}</p>
    </div>
  )
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card">
      <h3 className="text-sm font-display font-semibold text-neutral-900">{title}</h3>
      <div className="mt-3">{children}</div>
    </div>
  )
}

function AgingSection({ buckets }: { buckets: AgingBucketResponse[] }) {
  const total = buckets.reduce((sum, bucket) => sum + bucket.cents, 0)

  return (
    <SectionCard title="A/R aging">
      {total === 0 ? (
        <EmptySection message="Nothing is currently owed — every bucket would show what's outstanding by how long it's been owed." />
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {buckets.map((bucket) => (
            <li key={bucket.label} className="rounded-lg bg-neutral-50 p-3">
              <p className="text-xs text-neutral-500">{bucket.label} days</p>
              <p className="mt-1 text-sm font-medium text-neutral-900">
                {formatCents(bucket.cents, "usd")}
              </p>
              <p className="text-xs text-neutral-500">
                {bucket.count} {bucket.count === 1 ? "bill" : "bills"}
              </p>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

function PayerMixSection({ entries }: { entries: PayerMixEntryResponse[] }) {
  return (
    <SectionCard title="Payer mix">
      {entries.length === 0 ? (
        <EmptySection message="No claims billed to a payer in this window yet — billed and collected amounts per payer would show here." />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              <th className="py-1 font-normal">Payer</th>
              <th className="py-1 font-normal text-right">Billed</th>
              <th className="py-1 font-normal text-right">Collected</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.payer_id} className="border-t border-neutral-100">
                <td className="py-2 text-neutral-900">{entry.payer_name}</td>
                <td className="py-2 text-right text-neutral-900">
                  {formatCents(entry.billed_cents, "usd")}
                </td>
                <td className="py-2 text-right text-neutral-900">
                  {formatCents(entry.collected_cents, "usd")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  )
}

function CollectionsRateSection({
  collectionsRate,
}: {
  collectionsRate: BillingReportResponse["collections_rate"]
}) {
  const collectible =
    collectionsRate.billed_cents -
    collectionsRate.contractual_adjustment_cents -
    collectionsRate.write_off_cents
  const rate = collectible > 0 ? collectionsRate.collected_cents / collectible : null

  return (
    <SectionCard title="Collections rate">
      {collectible === 0 && collectionsRate.collected_cents === 0 ? (
        <EmptySection message="Nothing was billed in this window — the share collected of what was actually collectible would show here." />
      ) : (
        <div>
          <p className="text-2xl font-display font-semibold text-neutral-900">
            {rate === null ? "—" : `${(rate * 100).toFixed(1)}%`}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            {formatCents(collectionsRate.collected_cents, "usd")} collected of{" "}
            {formatCents(collectible, "usd")} collectible — billed less contractual adjustments
            and write-offs, since neither was ever collectible.
          </p>
        </div>
      )}
    </SectionCard>
  )
}

function ClaimLagSection({ byPayer }: { byPayer: PayerLagResponse[] }) {
  return (
    <SectionCard title="Claim-to-payment lag">
      {byPayer.length === 0 ? (
        <EmptySection message="No paid claims in this window yet — median and p90 days from submission to payment per payer would show here." />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              <th className="py-1 font-normal">Payer</th>
              <th className="py-1 font-normal text-right">Median days</th>
              <th className="py-1 font-normal text-right">P90 days</th>
              <th className="py-1 font-normal text-right">Claims</th>
            </tr>
          </thead>
          <tbody>
            {byPayer.map((payer) => (
              <tr key={payer.payer_id} className="border-t border-neutral-100">
                <td className="py-2 text-neutral-900">{payer.payer_name}</td>
                <td className="py-2 text-right text-neutral-900">
                  {payer.median_days.toFixed(1)}
                </td>
                <td className="py-2 text-right text-neutral-900">{payer.p90_days.toFixed(1)}</td>
                <td className="py-2 text-right text-neutral-900">{payer.claim_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  )
}

export function ReportsView() {
  const today = new Date()
  const [from, setFrom] = useState(firstOfMonth(today))
  const [to, setTo] = useState(isoDate(today))
  const [appliedRange, setAppliedRange] = useState({ from, to })

  const { data, isLoading, error } = useBillingReport(appliedRange.from, appliedRange.to)
  const rangeInvalid = !from || !to || to < from

  return (
    <div className="space-y-6">
      <div className="card">
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <Label htmlFor="report-from">From</Label>
            <Input
              id="report-from"
              type="date"
              value={from}
              max={to}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="report-to">To</Label>
            <Input
              id="report-to"
              type="date"
              value={to}
              min={from}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          <Button
            disabled={rangeInvalid}
            onClick={() => setAppliedRange({ from, to })}
            data-testid="reports-run"
          >
            <BarChart3 aria-hidden />
            Run report
          </Button>
        </div>
      </div>

      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {error && (
        <p className="text-sm text-red-500">
          {error instanceof Error ? error.message : "Failed to load the report."}
        </p>
      )}

      {data && (
        <div className="grid gap-6 sm:grid-cols-2">
          <AgingSection buckets={data.aging.buckets} />
          <PayerMixSection entries={data.payer_mix.entries} />
          <CollectionsRateSection collectionsRate={data.collections_rate} />
          <ClaimLagSection byPayer={data.claim_payment_lag.by_payer} />
        </div>
      )}
    </div>
  )
}
