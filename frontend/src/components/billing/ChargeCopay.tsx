// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ChargeCopay
 *
 * Taking a covered client's copay from the queue row, on the card already on
 * file. The visit still has to be claimed afterwards — a copay is a part
 * payment on a session the payer has yet to see — so this never removes the
 * row it sits on.
 *
 * The amount comes off the row (the practice's override, else what the payer
 * last said). When the row carries one the button says so and the charge is
 * sent without it: the server resolves the figure again from the same rule,
 * so a queue left open while somebody edited the coverage cannot charge a
 * stale amount. When it carries none, the clinician is asked — the one
 * remaining source, and better than guessing at a coinsurance or a deductible
 * nobody can compute at the door.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError } from "@/lib/api/client"
import { dollarsToCents, formatCents } from "@/lib/money"
import { declineReason } from "@/lib/paymentDisplay"
import { useCreateCharge } from "@/hooks/usePayments"
import type { UnbilledSessionItem } from "@/types/billing"

/**
 * True when the row can offer to collect a copay at all.
 *
 * A zero copay is a real answer and not one to charge: the payer priced this
 * benefit at nothing, so there is nothing to take at the door. `null` is the
 * other thing entirely — nobody has said, so the amount is asked for.
 */
export function offersCopay(item: UnbilledSessionItem): boolean {
  return item.has_coverage && item.copay_cents !== 0
}

export function ChargeCopay({ item }: { item: UnbilledSessionItem }) {
  const charge = useCreateCharge()
  const [prompting, setPrompting] = useState(false)
  const [typedAmount, setTypedAmount] = useState("")
  const [charged, setCharged] = useState<number | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  const known = item.copay_cents

  async function chargeCopay(amountCents: number | null) {
    setFailure(null)
    try {
      const row = await charge.mutateAsync({
        patientId: item.patient_id,
        data: {
          kind: "copay",
          appointment_id: item.appointment_id ?? undefined,
          // Sent only when the clinician typed one, which is the case the
          // server has no answer for.
          amount_cents: amountCents ?? undefined,
        },
      })
      if (row.status === "succeeded") {
        setCharged(row.amount_cents)
        setPrompting(false)
      } else {
        setFailure(declineReason(row))
      }
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? error.message
          : "The copay could not be charged. Check the client's charges before retrying.",
      )
    }
  }

  if (charged !== null) {
    return (
      <span className="text-sm text-secondary-700" data-testid="copay-charged">
        Copay {formatCents(charged, item.currency)} charged
      </span>
    )
  }

  const typedCents = dollarsToCents(typedAmount)

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        data-testid="charge-copay"
        disabled={charge.isPending}
        onClick={() => (known === null ? setPrompting(true) : chargeCopay(null))}
      >
        {charge.isPending
          ? "Charging…"
          : known === null
            ? "Charge copay"
            : `Charge copay ${formatCents(known, item.currency)}`}
      </Button>

      {failure && (
        <span role="alert" className="text-sm text-red-600">
          {failure}
        </span>
      )}

      <Dialog open={prompting} onOpenChange={setPrompting}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>What is the copay?</DialogTitle>
            <DialogDescription>
              Neither this client&rsquo;s coverage nor the last eligibility check says what they
              pay at the door. Put it on the coverage to stop being asked.
            </DialogDescription>
          </DialogHeader>
          <div className="form-group">
            <Label htmlFor="copay-amount">Amount</Label>
            <Input
              id="copay-amount"
              inputMode="decimal"
              placeholder="0.00"
              value={typedAmount}
              onChange={(event) => setTypedAmount(event.target.value)}
              className="max-w-40"
            />
          </div>
          {failure && (
            <p role="alert" className="text-sm text-red-600">
              {failure}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setPrompting(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              data-testid="charge-typed-copay"
              disabled={typedCents === null || charge.isPending}
              onClick={() => typedCents !== null && chargeCopay(typedCents)}
            >
              {charge.isPending ? "Charging…" : "Charge copay"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
