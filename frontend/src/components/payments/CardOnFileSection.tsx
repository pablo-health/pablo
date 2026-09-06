// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * CardOnFileSection
 *
 * The card a practice keeps on file for one client: brand, last four and
 * expiry, or an empty state offering to add one. Those three fields are the
 * whole of what exists — the card itself lives at the processor, and adding or
 * replacing one goes through `AddCardDialog`, which this application's own
 * code never gets to read.
 */

"use client"

import { useState } from "react"
import { CreditCard } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { useReadOnlyMode } from "@/lib/access/readOnlyMode"
import { isPaymentsUnconfigured } from "@/lib/api/payments"
import { formatCard, formatCardExpiry } from "@/lib/paymentDisplay"
import { usePatientCard } from "@/hooks/usePayments"
import { AddCardDialog } from "./AddCardDialog"

interface CardOnFileSectionProps {
  patientId: string
}

export function CardOnFileSection({ patientId }: CardOnFileSectionProps) {
  const { data: card, isLoading, error } = usePatientCard(patientId)
  const { readOnly } = useReadOnlyMode()
  const [dialogOpen, setDialogOpen] = useState(false)

  if (isLoading) return <Skeleton className="h-16 w-full" />

  if (isPaymentsUnconfigured(error)) {
    return (
      <p className="text-sm text-neutral-600">
        Card payments are not set up for this practice.
      </p>
    )
  }

  if (error) {
    return (
      <p className="text-sm text-red-500">
        {error instanceof Error ? error.message : "Failed to load the card on file."}
      </p>
    )
  }

  return (
    <div className="space-y-3">
      {card ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-neutral-100 px-3 py-2.5">
          <span className="flex items-center gap-2">
            <CreditCard className="h-4 w-4 text-neutral-400" />
            <span className="text-sm font-medium text-neutral-900">{formatCard(card)}</span>
            <span className="text-xs text-neutral-500">
              Expires {formatCardExpiry(card)}
            </span>
          </span>
          {!readOnly && (
            <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
              Replace card
            </Button>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-8 text-center">
          <CreditCard className="h-8 w-8 text-neutral-300" />
          <p className="text-sm text-neutral-600">No card on file for this client.</p>
          {!readOnly && <Button onClick={() => setDialogOpen(true)}>Add a card</Button>}
        </div>
      )}

      <AddCardDialog
        patientId={patientId}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        replacing={!!card}
      />
    </div>
  )
}
