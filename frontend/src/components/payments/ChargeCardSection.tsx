// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChargeCardSection
 *
 * Charging the card on file, on the note that has just been signed. The
 * session happened, the note is done, and this is the next thing the clinician
 * does — so it sits at the end of the same column as the finalize action,
 * appears only once the note carries a `finalized_at`, and asks for exactly
 * one decision.
 *
 * The amount is shown before it is charged, resolved by the backend from the
 * same rule the charge itself uses. Where no rate is set anywhere the
 * clinician types one rather than being shown a disabled button with no way
 * forward — and nothing here ever charges on its own: every charge, including
 * a retry after a decline, is a click.
 */

"use client"

import { useState } from "react"
import { AlertCircle, Check, CreditCard } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { isPaymentsUnconfigured } from "@/lib/api/payments"
import { dollarsToCents, formatCents } from "@/lib/money"
import { declineReason, formatCard } from "@/lib/paymentDisplay"
import { useChargeAmount, useCreateCharge, usePatientCard } from "@/hooks/usePayments"
import type { ChargeResponse } from "@/types/payments"
import { AddCardDialog } from "./AddCardDialog"

interface ChargeCardSectionProps {
  patientId: string
}

export function ChargeCardSection({ patientId }: ChargeCardSectionProps) {
  const card = usePatientCard(patientId)
  const amount = useChargeAmount(patientId)
  const charge = useCreateCharge()
  const { readOnly } = useReadOnlyMode()

  const [dialogOpen, setDialogOpen] = useState(false)
  const [typedAmount, setTypedAmount] = useState("")
  const [result, setResult] = useState<ChargeResponse | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  // Nothing to say on a signed note in a deployment that does not take cards.
  if (isPaymentsUnconfigured(card.error) || isPaymentsUnconfigured(amount.error)) return null
  if (readOnly) return null

  if (card.isLoading || amount.isLoading) {
    return <Skeleton className="h-20 w-full" />
  }

  const resolvedCents = amount.data?.amount_cents ?? null
  const currency = amount.data?.currency ?? "usd"
  const typedCents = dollarsToCents(typedAmount)
  const chargeCents = resolvedCents ?? typedCents

  async function handleCharge() {
    if (chargeCents === null) return
    setFailure(null)
    setResult(null)
    try {
      const row = await charge.mutateAsync({
        patientId,
        // The resolved amount is deliberately NOT echoed back: the backend
        // resolves it again from the same rule, and sending the previewed
        // figure would let a stale one be charged. An amount is sent only when
        // the clinician typed one, which is the case the backend has no answer
        // for.
        data: resolvedCents === null ? { amount_cents: chargeCents } : {},
      })
      setResult(row)
    } catch {
      // A decline comes back as a `failed` row, not an exception — reaching
      // here means the attempt itself did not complete, so it is not known
      // whether anything was charged and the ledger is where to look.
      setFailure("The charge could not be completed. Check the client's charges before retrying.")
    }
  }

  if (!card.data) {
    return (
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-neutral-900">Payment</h3>
        <p className="text-sm text-neutral-600">
          No card on file for this client.
        </p>
        <Button variant="outline" onClick={() => setDialogOpen(true)}>
          <CreditCard className="mr-2 h-4 w-4" />
          Add a card
        </Button>
        <AddCardDialog patientId={patientId} open={dialogOpen} onOpenChange={setDialogOpen} />
      </div>
    )
  }

  if (result?.status === "succeeded") {
    return (
      <div className="space-y-2">
        <h3 className="text-lg font-semibold text-neutral-900">Payment</h3>
        <p className="flex items-center gap-2 text-sm text-secondary-700">
          <Check className="h-4 w-4" />
          Charged {formatCents(result.amount_cents, result.currency)} to{" "}
          {formatCard(card.data)}.
        </p>
      </div>
    )
  }

  const declined = result?.status === "failed"

  return (
    <div className="space-y-3">
      <h3 className="text-lg font-semibold text-neutral-900">Payment</h3>

      <p className="flex items-center gap-2 text-sm text-neutral-600">
        <CreditCard className="h-4 w-4 text-neutral-400" />
        {formatCard(card.data)}
      </p>

      {resolvedCents === null && (
        <div className="space-y-1">
          <Label htmlFor="charge-amount">Amount</Label>
          <Input
            id="charge-amount"
            inputMode="decimal"
            placeholder="0.00"
            value={typedAmount}
            onChange={(event) => setTypedAmount(event.target.value)}
            className="max-w-40"
          />
          <p className="text-xs text-neutral-500">
            No rate is set for this client, so there is nothing to charge by
            default.
          </p>
        </div>
      )}

      {declined && result && (
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

      <Button
        onClick={handleCharge}
        disabled={chargeCents === null || charge.isPending}
        className="bg-secondary-600 hover:bg-secondary-700 text-white"
      >
        {charge.isPending
          ? "Charging..."
          : declined
            ? "Try again"
            : chargeCents === null
              ? "Charge card"
              : `Charge ${formatCents(chargeCents, currency)}`}
      </Button>
    </div>
  )
}
