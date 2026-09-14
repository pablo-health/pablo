// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { useBillingProfile } from "@/hooks/useBillingProfile"
import type { PayerResponse } from "@/types/coverage"

/**
 * The one step in payer setup that moves money information, asked for on its
 * own.
 *
 * Filing an 835 enrollment tells a payer to send its remittances HERE. They
 * then stop arriving wherever they arrive today — an old clearinghouse, a
 * billing service, or the platform that has been handling her insurance side.
 * Nothing else in this card has a consequence outside Pablo, which is why a
 * general "Enroll with payer" is no longer enough to do it: pressing one
 * button that files eligibility, claims and remittances together makes the
 * consequential one look like the other two.
 *
 * It says what is actually being requested, under which identity, because
 * "enrolled" is not a thing she can inspect afterwards — and a request filed
 * under the wrong NPI or tax id is discovered when the money does not arrive.
 *
 * ON THE ROUTING LEVEL, AND WHY IT ADMITS IGNORANCE. Some payers route
 * remittances by tax id rather than by NPI, and for those, enrolling moves
 * every clinician billing under it rather than only her. The payer row does
 * not carry that fact — the directory knows it at pick time, this card does
 * not — so rather than reassure her with something we have not checked, it
 * says we cannot tell and asks her to confirm she knows where they go today.
 * Guessing in the reassuring direction is how a colleague finds out by not
 * being paid.
 */
export function RemittanceCutoverDialog({
  payer,
  open,
  onOpenChange,
  onConfirm,
  pending,
}: {
  payer: PayerResponse
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  pending?: boolean
}) {
  const { data: profile } = useBillingProfile()
  // A fresh decision every time. The card mounts this only while she is
  // confirming, so closing it discards the tick — which is the point: a second
  // payer's remittances must not move on a box she ticked about the first.
  const [understood, setUnderstood] = useState(false)

  const billingNpi = profile?.billing_npi?.trim()
  const taxIdLast4 = profile?.tax_id_last4

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="remittance-cutover">
        <DialogHeader>
          <DialogTitle>Send {payer.name}&rsquo;s payment reports to Pablo?</DialogTitle>
          <DialogDescription>
            An electronic remittance advice, or ERA, explains how a payer handled your claims and
            what it paid. This request asks {payer.name} to start sending those reports to Pablo.
          </DialogDescription>
        </DialogHeader>

        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted-foreground">Payer</dt>
          <dd className="m-0 text-foreground">
            {payer.name} &middot; ID {payer.payer_id}
          </dd>

          <dt className="text-muted-foreground">Billing details</dt>
          <dd className="m-0 text-foreground" data-testid="cutover-identity">
            {billingNpi ? `NPI ${billingNpi}` : "NPI missing"}
            {taxIdLast4 ? `, tax ID ending in ${taxIdLast4}` : ", tax ID missing"}
          </dd>

          <dt className="text-muted-foreground">New destination</dt>
          <dd className="m-0 text-foreground">Pablo</dd>
        </dl>

        <p className="text-[12.5px] text-amber-800">
          This may stop the reports from going to the service, clearinghouse, or biller that
          receives them today. If someone uses those reports to record your payments, check with
          them before continuing.
        </p>

        {/* Said plainly rather than guessed at. See the class docstring. */}
        <p className="text-[12.5px] text-muted-foreground" data-testid="cutover-routing-unknown">
          We can&rsquo;t tell whether {payer.name} will make this change only for this NPI or for
          every clinician using this tax ID. If other clinicians use the same tax ID, their
          reports could move too.
        </p>

        {(!billingNpi || !taxIdLast4) && (
          <p className="text-[12.5px] text-destructive" data-testid="cutover-identity-missing">
            Add the missing billing details before sending this request.
          </p>
        )}

        <div className="flex items-start gap-2.5">
          <Checkbox
            id="cutover-understood"
            checked={understood}
            onCheckedChange={(checked) => setUnderstood(checked === true)}
            className="mt-0.5"
            data-testid="cutover-understood"
          />
          <Label htmlFor="cutover-understood" className="font-normal">
            I know who receives these reports today and have checked what this change will affect.
          </Label>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={!understood || pending || !billingNpi || !taxIdLast4}
            data-testid="cutover-confirm"
          >
            {pending ? "Sending..." : "Send reports to Pablo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
