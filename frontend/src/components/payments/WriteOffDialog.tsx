// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * WriteOffDialog
 *
 * Write off part or all of a client's balance. One client, one amount,
 * chosen deliberately — there is no bulk form, and none is planned.
 *
 * The reason picker is the fixed set the ledger's CHECK constraint allows.
 * `courtesy` and `small_balance` can come back refused: the practice has not
 * opted into courtesy waivers, or this balance is over the small-balance
 * threshold. Either refusal surfaces as the server's own explanation rather
 * than a guess at one, since only the server knows the practice's policy.
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
import { useCreateWriteOff } from "@/hooks/usePayments"
import { centsToDollars, dollarsToCents } from "@/lib/money"
import { ApiError } from "@/lib/api/client"
import type { WriteOffReason } from "@/types/payments"

const REASON_LABELS: Record<WriteOffReason, string> = {
  hardship: "Financial hardship",
  small_balance: "Small balance — not worth chasing",
  courtesy: "Courtesy",
  error: "Billing error",
}

const REASONS: WriteOffReason[] = ["hardship", "small_balance", "courtesy", "error"]

interface WriteOffDialogProps {
  patientId: string
  balanceCents: number
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function WriteOffDialog({ patientId, balanceCents, open, onOpenChange }: WriteOffDialogProps) {
  const createWriteOff = useCreateWriteOff()
  const [amount, setAmount] = useState(() => centsToDollars(balanceCents))
  const [reason, setReason] = useState<WriteOffReason | "">("")
  const [note, setNote] = useState("")
  const [problem, setProblem] = useState<string | null>(null)

  function reset() {
    setAmount(centsToDollars(balanceCents))
    setReason("")
    setNote("")
    setProblem(null)
  }

  function handleOpenChange(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  function handleSubmit() {
    const amountCents = dollarsToCents(amount)
    if (amountCents === null) {
      setProblem("Enter a dollar amount, like 30.00.")
      return
    }
    if (!reason) {
      setProblem("Choose a reason.")
      return
    }
    setProblem(null)
    createWriteOff.mutate(
      { patientId, data: { amount_cents: amountCents, reason, note: note.trim() || undefined } },
      {
        onSuccess: () => handleOpenChange(false),
        onError: (error) => {
          setProblem(
            error instanceof ApiError && error.message
              ? error.message
              : "The write-off could not be recorded.",
          )
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Write off balance</DialogTitle>
          <DialogDescription>
            Reduces what this client owes without collecting it. Every write-off is recorded
            with its reason.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="write-off-amount">Amount</Label>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">$</span>
              <Input
                id="write-off-amount"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                inputMode="decimal"
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="write-off-reason">Reason</Label>
            <Select value={reason} onValueChange={(value) => setReason(value as WriteOffReason)}>
              <SelectTrigger id="write-off-reason" className="w-full">
                <SelectValue placeholder="Choose a reason" />
              </SelectTrigger>
              <SelectContent>
                {REASONS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {REASON_LABELS[value]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="write-off-note">Note</Label>
            <Textarea
              id="write-off-note"
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
          <Button onClick={handleSubmit} disabled={createWriteOff.isPending}>
            {createWriteOff.isPending ? "Writing off..." : "Write off"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
