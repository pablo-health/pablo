// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * BalanceTab
 *
 * What the client owes, and the rows it is made of, grouped by the visit
 * they belong to. A total on its own is a number to argue with; the rows
 * behind it are what answer "why".
 *
 * Two actions sit on it, both about the whole balance rather than any one
 * row: charge the card for all of it, or produce the statement the client
 * gets handed. There is no partial payment here on purpose — splitting a
 * balance is an allocation decision, and offering it without one would just
 * move the argument.
 */

"use client"

import { useState } from "react"
import Link from "next/link"
import { AlertCircle, Check, CreditCard, FileText, ReceiptText } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { isPaymentsUnconfigured, fetchStatement, STATEMENT_FILENAME } from "@/lib/api/payments"
import { formatCents } from "@/lib/money"
import {
  chargeKindLabel,
  chargeStatusBadge,
  declineReason,
  formatBalanceLine,
  formatChargeDate,
} from "@/lib/paymentDisplay"
import {
  useChargeBalance,
  usePatientBalance,
  usePatientCard,
  usePatientCharges,
} from "@/hooks/usePayments"
import type { ChargeResponse, VisitBalanceResponse } from "@/types/payments"
import { WriteOffDialog } from "./WriteOffDialog"

interface BalanceTabProps {
  patientId: string
}

/** Rows that hang off no visit, collected under one heading. */
const UNATTRIBUTED = "Other charges"

export function BalanceTab({ patientId }: BalanceTabProps) {
  const balance = usePatientBalance(patientId)
  const charges = usePatientCharges(patientId)
  const card = usePatientCard(patientId)
  const chargeBalance = useChargeBalance()
  const { readOnly } = useReadOnlyMode()

  const [result, setResult] = useState<ChargeResponse | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [statementError, setStatementError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [writeOffOpen, setWriteOffOpen] = useState(false)

  if (balance.isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }

  if (balance.error || !balance.data) {
    return (
      <p className="text-sm text-red-500">
        {balance.error instanceof Error
          ? balance.error.message
          : "Failed to load this client's balance."}
      </p>
    )
  }

  const owed = balance.data.balance_cents
  const line = formatBalanceLine(owed)
  // The card routes 503 on a deployment that takes no cards at all. The
  // balance itself still totals — a practice that only bills insurance has
  // clients who owe it money — so only the charge action goes away.
  const cardsUnavailable = isPaymentsUnconfigured(card.error)
  const canCharge =
    !readOnly && !cardsUnavailable && owed > 0 && Boolean(card.data?.chargeable)

  async function handleCharge() {
    setFailure(null)
    setResult(null)
    try {
      setResult(await chargeBalance.mutateAsync({ patientId }))
    } catch {
      // A decline resolves with a `failed` row; reaching here means the
      // attempt did not complete at all, so it is not known whether anything
      // was charged and the ledger is where to look.
      setFailure(
        "The charge could not be completed. Check this client's charges before retrying.",
      )
    }
  }

  async function handleStatement() {
    setStatementError(null)
    setDownloading(true)
    try {
      saveBlob(await fetchStatement(patientId), STATEMENT_FILENAME)
    } catch {
      setStatementError("The statement could not be generated.")
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-lg font-semibold text-neutral-900" data-testid="balance-total">
          {line ?? "Nothing owed"}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={handleStatement} disabled={downloading}>
            <FileText className="mr-2 h-4 w-4" />
            {downloading ? "Preparing..." : "Statement"}
          </Button>
          {!readOnly && owed > 0 && (
            <Button variant="outline" onClick={() => setWriteOffOpen(true)}>
              <ReceiptText className="mr-2 h-4 w-4" />
              Write off
            </Button>
          )}
          {!readOnly && !cardsUnavailable && owed > 0 && (
            <Button
              onClick={handleCharge}
              disabled={!canCharge || chargeBalance.isPending}
              className="bg-secondary-600 hover:bg-secondary-700 text-white"
            >
              <CreditCard className="mr-2 h-4 w-4" />
              {chargeBalance.isPending
                ? "Charging..."
                : `Charge balance (${formatCents(owed)})`}
            </Button>
          )}
        </div>
      </section>

      {!readOnly && !cardsUnavailable && owed > 0 && !card.data?.chargeable && (
        <p className="text-sm text-neutral-500">
          No card on file for this client, so the balance cannot be charged here.
        </p>
      )}

      {result?.status === "succeeded" && (
        <p className="flex items-center gap-2 text-sm text-secondary-700">
          <Check className="h-4 w-4" />
          Charged {formatCents(result.amount_cents, result.currency)}.
        </p>
      )}
      {result?.status === "failed" && (
        <p role="alert" className="flex items-start gap-2 text-sm text-red-600">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {declineReason(result)}
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-start gap-2 text-sm text-red-600">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {failure}
        </p>
      )}
      {statementError && (
        <p role="alert" className="flex items-start gap-2 text-sm text-red-600">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {statementError}
        </p>
      )}

      <Ledger visits={balance.data.by_visit} charges={charges.data ?? []} />

      {owed > 0 && (
        <WriteOffDialog
          patientId={patientId}
          balanceCents={owed}
          open={writeOffOpen}
          onOpenChange={setWriteOffOpen}
        />
      )}
    </div>
  )
}

function Ledger({
  visits,
  charges,
}: {
  visits: VisitBalanceResponse[]
  charges: ChargeResponse[]
}) {
  if (charges.length === 0) {
    return <p className="text-sm text-neutral-500">No charges yet.</p>
  }

  return (
    <div className="space-y-4">
      {visits.map((visit) => {
        const rows = charges.filter((c) => c.appointment_id === visit.appointment_id)
        if (rows.length === 0) return null
        return (
          <section key={visit.appointment_id ?? UNATTRIBUTED}>
            <div className="mb-2 flex items-baseline justify-between gap-3">
              <h4 className="text-sm font-semibold text-neutral-900">
                {visit.appointment_id ? "Visit" : UNATTRIBUTED}
              </h4>
              <span className="text-sm text-neutral-600">
                {formatBalanceLine(visit.balance_cents) ?? "Settled"}
              </span>
            </div>
            <ul className="space-y-2">
              {rows.map((charge) => (
                <LedgerRow key={charge.id} charge={charge} />
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}

function LedgerRow({ charge }: { charge: ChargeResponse }) {
  const badge = chargeStatusBadge(charge.status)
  return (
    <li className="rounded-lg border border-neutral-100 px-3 py-2.5">
      <div className="flex items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-medium text-neutral-900">
            {chargeKindLabel(charge.kind)}
          </span>
          {charge.claim_id && (
            <Link
              href={`/dashboard/billing/claims/${charge.claim_id}`}
              className="text-xs text-primary-700 transition-colors hover:text-primary-900"
            >
              View claim
            </Link>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-3">
          <span className="text-sm text-neutral-900">
            {formatCents(charge.amount_cents, charge.currency)}
          </span>
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
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
