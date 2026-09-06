// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The claims tracker — every claim, newest first, with where it stands, how
 * old it is, what to do next and the deadline that binds it. A row links to
 * the claim's own page, where the actions live.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { AlertCircle, FileText } from "lucide-react"
import { useClaims } from "@/hooks/useClaims"
import { formatCents } from "@/lib/money"
import { CLAIM_STATES, type ClaimState, type ClaimTrackerItem } from "@/types/claims"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ClaimStateBadge, DeadlineBadge } from "./ClaimBadges"
import { ageInDays, formatIsoDate, frequencyLabel, presentState } from "./claimPresentation"

export function ClaimsTracker() {
  const [state, setState] = useState<ClaimState | "">("")
  const { data, isLoading } = useClaims(state ? { state } : {})

  const rows = data?.data ?? []

  return (
    <div className="card space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-display font-semibold text-neutral-900">Claims</h2>
          <p className="mt-1 text-sm text-neutral-600">
            Every claim filed from here, newest first. A claim is queued until the
            clearinghouse takes it, then moves on each receipt.
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="claims-state-filter">Show</Label>
          <select
            id="claims-state-filter"
            className="block rounded-md border border-neutral-300 bg-white px-2 py-1.5 text-sm"
            value={state}
            onChange={(e) => setState(e.target.value as ClaimState | "")}
          >
            <option value="">All claims</option>
            {CLAIM_STATES.map((s) => (
              <option key={s} value={s}>
                {presentState(s).label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <div className="py-8 text-center">
          <FileText className="mx-auto h-8 w-8 text-neutral-300" />
          <p className="mt-3 text-sm font-medium text-neutral-900">No claims yet</p>
          <p className="mt-1 text-sm text-neutral-500">
            File one from an unbilled session whose client has coverage on file.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Claim</TableHead>
                <TableHead>Client</TableHead>
                <TableHead>Service date</TableHead>
                <TableHead>Payer</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Age</TableHead>
                <TableHead>Next</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TrackerRow key={row.id} row={row} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  )
}

function TrackerRow({ row }: { row: ClaimTrackerItem }) {
  const presentation = presentState(row.state)
  const kind = frequencyLabel(row.frequency_code)
  const age = ageInDays(row.created_at)
  return (
    <TableRow data-testid="claims-tracker-row" data-claim-id={row.id} data-state={row.state}>
      <TableCell>
        <Link
          href={`/dashboard/billing/claims/${row.id}`}
          className="font-mono text-xs text-neutral-900 underline-offset-2 hover:underline"
        >
          {row.control_number}
        </Link>
        {kind && <span className="ml-2 text-xs text-neutral-500">{kind}</span>}
        <span className="ml-2 text-xs text-neutral-500">
          {formatCents(row.total_charge_cents)}
        </span>
      </TableCell>
      <TableCell className="text-neutral-900">{row.patient_name}</TableCell>
      <TableCell className="text-neutral-700">
        {row.service_date ? formatIsoDate(row.service_date) : "—"}
      </TableCell>
      <TableCell className="text-neutral-700">{row.payer_name ?? "Unknown payer"}</TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1">
          <ClaimStateBadge state={row.state} />
          {presentation.alert && (
            <AlertCircle
              className="h-4 w-4 text-red-600"
              aria-label="Needs attention"
              data-testid="claim-alert"
            />
          )}
        </div>
      </TableCell>
      <TableCell className="text-neutral-700">
        {age === 0 ? "Today" : `${age} ${age === 1 ? "day" : "days"}`}
      </TableCell>
      <TableCell>
        <div className="flex flex-col items-start gap-1">
          {presentation.nextAction && (
            <span className="text-sm text-neutral-700">{presentation.nextAction}</span>
          )}
          <DeadlineBadge deadlines={row.deadlines} state={row.state} />
        </div>
      </TableCell>
    </TableRow>
  )
}
