// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PaymentsTab
 *
 * Chart tab for self-pay: the card on file, and what has been charged to it.
 * Charging itself lives on the signed note, where the clinician already knows
 * the session happened — this tab is where the card is set up and the ledger
 * is read.
 */

"use client"

import { CardOnFileSection } from "./CardOnFileSection"
import { ChargeHistory } from "./ChargeHistory"

interface PaymentsTabProps {
  patientId: string
}

export function PaymentsTab({ patientId }: PaymentsTabProps) {
  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-3 text-sm font-semibold text-neutral-900">Card on file</h3>
        <CardOnFileSection patientId={patientId} />
      </section>
      <section>
        <h3 className="mb-3 text-sm font-semibold text-neutral-900">Charges</h3>
        <ChargeHistory patientId={patientId} />
      </section>
    </div>
  )
}
