// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the patient sees once the form has gone.
 *
 * The receipt code is the part worth keeping: eight characters that unlock
 * nothing and exist so a person on the phone and a person at the chart can
 * name the same form. It is not a credential, which is why it can be read
 * out loud and shown here.
 *
 * The 200 also carries each measure's total and its band, and this screen
 * shows neither. A PHQ-9 total is a number with a clinical meaning attached,
 * and the person qualified to attach it is the clinician reading the chart —
 * not a page a patient meets alone, minutes after answering nine questions
 * about how bad the last fortnight has been.
 *
 * What it does show is any note the server sent, as the server wrote it.
 * There is one today and it appears on almost no submission: a form that
 * branches can be answered in an order that leaves an answer behind the
 * patient's own later change, and that answer is not handed in. Wording it
 * here would put a second copy of that sentence a step away from the code
 * that decides when it is true.
 */

import { Button } from "@/components/ui/button"
import type { IntakeReceipt } from "@/lib/api/patientIntake"
import { CrisisFooter } from "./CrisisFooter"
import {
  RECEIPT_BODY,
  RECEIPT_CLOSE,
  RECEIPT_CODE_LABEL,
  RECEIPT_CODE_NOTE,
  RECEIPT_HEADING,
} from "./formsCopy"

interface ReceiptScreenProps {
  receipt: IntakeReceipt
  onClose: () => void
}

export function ReceiptScreen({ receipt, onClose }: ReceiptScreenProps) {
  const notes = receipt.notes ?? []
  return (
    <section data-testid="forms-receipt" aria-labelledby="forms-receipt-heading">
      <h2 id="forms-receipt-heading" className="text-lg font-semibold text-neutral-900">
        {RECEIPT_HEADING}
      </h2>
      <p className="mt-2 text-sm text-neutral-700">{RECEIPT_BODY}</p>

      <div className="mt-4 rounded-md border border-neutral-200 p-4">
        <p className="text-xs font-medium text-neutral-500">{RECEIPT_CODE_LABEL}</p>
        <p
          data-testid="forms-receipt-code"
          className="mt-1 font-mono text-lg tracking-widest text-neutral-900"
        >
          {receipt.receipt_code}
        </p>
        <p className="mt-2 text-xs text-neutral-500">{RECEIPT_CODE_NOTE}</p>
      </div>

      {notes.length > 0 && (
        <ul data-testid="forms-receipt-notes" className="mt-4 space-y-2">
          {notes.map((note) => (
            <li key={note} className="text-sm text-neutral-700">
              {note}
            </li>
          ))}
        </ul>
      )}

      <CrisisFooter />

      <Button
        variant="outline"
        className="mt-6 w-full"
        data-testid="forms-receipt-close"
        onClick={onClose}
      >
        {RECEIPT_CLOSE}
      </Button>
    </section>
  )
}
