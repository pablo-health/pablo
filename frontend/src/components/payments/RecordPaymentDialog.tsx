// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecordPaymentDialog
 *
 * Write down money the practice already has — a cheque at the end of a
 * session, cash, a transfer. Nothing is charged here and no card is needed,
 * which is the point: a practice that does not keep clients' cards on file
 * has no other way to say it was paid.
 *
 * Card is deliberately absent from the picker. A card payment is recorded
 * when it goes through, and a hand-written one would read identically on a
 * statement while having no charge behind it. The server refuses it too.
 *
 * The amount defaults to the balance but is not capped at it. Paying ahead
 * for a block of sessions is ordinary, and the credit it leaves is real —
 * clamping it here would make the ledger disagree with the bank.
 */

"use client"

import { useState } from "react"
import { AlertCircle } from "lucide-react"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useRecordPayment } from "@/hooks/usePayments"
import { centsToDollars, dollarsToCents } from "@/lib/money"
import { ApiError } from "@/lib/api/client"
import { RECORDABLE_PAYMENT_METHODS } from "@/types/payments"
import type { RecordPaymentRequest } from "@/types/payments"

type RecordableMethod = RecordPaymentRequest["method"]

const METHOD_LABELS: Record<RecordableMethod, string> = {
  cash: "Cash",
  check: "Check",
  other: "Something else",
}

/** Only `other` has to be labelled — the rest are already self-describing. */
const REFERENCE_LABELS: Record<RecordableMethod, string> = {
  cash: "Reference",
  check: "Check number",
  other: "How it arrived",
}

const REFERENCE_PLACEHOLDERS: Record<RecordableMethod, string> = {
  cash: "Optional",
  check: "Optional — e.g. 1042",
  other: "e.g. Zelle, 14 Mar",
}

interface RecordPaymentDialogProps {
  patientId: string
  balanceCents: number
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function RecordPaymentDialog({
  patientId,
  balanceCents,
  open,
  onOpenChange,
}: RecordPaymentDialogProps) {
  const recordPayment = useRecordPayment()
  // Defaults to the balance, but only when one is owed: a client in credit
  // would otherwise see a negative figure pre-filled as the amount to record.
  const [amount, setAmount] = useState(() => centsToDollars(Math.max(balanceCents, 0)))
  const [method, setMethod] = useState<RecordableMethod | "">("")
  const [reference, setReference] = useState("")
  const [note, setNote] = useState("")
  const [problem, setProblem] = useState<string | null>(null)

  function reset() {
    setAmount(centsToDollars(Math.max(balanceCents, 0)))
    setMethod("")
    setReference("")
    setNote("")
    setProblem(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  function handleSubmit() {
    const amountCents = dollarsToCents(amount)
    if (amountCents === null || amountCents <= 0) {
      setProblem("Enter a dollar amount, like 150.00.")
      return
    }
    if (!method) {
      setProblem("Choose how the money arrived.")
      return
    }
    if (method === "other" && !reference.trim()) {
      setProblem("Say how the money arrived.")
      return
    }
    setProblem(null)
    recordPayment.mutate(
      {
        patientId,
        data: {
          amount_cents: amountCents,
          method,
          reference: reference.trim() || undefined,
          note: note.trim() || undefined,
        },
      },
      {
        onSuccess: () => handleOpenChange(false),
        onError: (error) => {
          setProblem(
            error instanceof ApiError && error.message
              ? error.message
              : "The payment could not be recorded.",
          )
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Record a payment</DialogTitle>
          <DialogDescription>
            Money you have already taken. Nothing is charged here.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="record-payment-amount">Amount</Label>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">$</span>
              <Input
                id="record-payment-amount"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                inputMode="decimal"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="record-payment-method">How it arrived</Label>
            <Select
              value={method}
              onValueChange={(value) => setMethod(value as RecordableMethod)}
            >
              <SelectTrigger id="record-payment-method" className="w-full">
                <SelectValue placeholder="Choose one" />
              </SelectTrigger>
              <SelectContent>
                {RECORDABLE_PAYMENT_METHODS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {METHOD_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {method && (
            <div className="space-y-1.5">
              <Label htmlFor="record-payment-reference">{REFERENCE_LABELS[method]}</Label>
              <Input
                id="record-payment-reference"
                value={reference}
                onChange={(event) => setReference(event.target.value)}
                placeholder={REFERENCE_PLACEHOLDERS[method]}
                maxLength={64}
              />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="record-payment-note">Note</Label>
            <Textarea
              id="record-payment-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Optional — shown on this client's ledger, never sent to a payer."
            />
          </div>
          {problem && (
            <p role="alert" className="flex items-start gap-2 text-sm text-red-600">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {problem}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={recordPayment.isPending}>
            {recordPayment.isPending ? "Recording..." : "Record payment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
