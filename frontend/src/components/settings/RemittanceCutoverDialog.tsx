// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
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
  const [understood, setUnderstood] = useState(false)

  // A fresh decision every time it opens. Carrying the tick over would mean a
  // second payer's remittances moved on a box she ticked about the first.
  useEffect(() => {
    if (open) setUnderstood(false)
  }, [open])

  const billingNpi = profile?.billing_npi?.trim()
  const taxIdLast4 = profile?.tax_id_last4

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="remittance-cutover">
        <DialogHeader>
          <DialogTitle>Move {payer.name}&rsquo;s remittances to Pablo?</DialogTitle>
          <DialogDescription>
            This files an enrollment asking {payer.name} to send its electronic remittance advice
            here from now on.
          </DialogDescription>
        </DialogHeader>

        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted-foreground">Payer</dt>
          <dd className="m-0 text-foreground">
            {payer.name} &middot; ID {payer.payer_id}
          </dd>

          <dt className="text-muted-foreground">Requested under</dt>
          <dd className="m-0 text-foreground" data-testid="cutover-identity">
            {billingNpi ? `NPI ${billingNpi}` : "no billing NPI on file"}
            {taxIdLast4 ? `, tax ID ending ${taxIdLast4}` : ", no tax ID on file"}
          </dd>

          <dt className="text-muted-foreground">Sent to</dt>
          <dd className="m-0 text-foreground">Pablo</dd>
        </dl>

        <p className="text-[12.5px] text-amber-800">
          Anything receiving this payer&rsquo;s remittances today stops receiving them &mdash; an
          old clearinghouse, a billing service, or a platform handling your insurance side. If
          somebody posts your payments from those files, tell them before you do this.
        </p>

        {/* Said plainly rather than guessed at. See the class docstring. */}
        <p className="text-[12.5px] text-muted-foreground" data-testid="cutover-routing-unknown">
          We can&rsquo;t tell from here whether {payer.name} routes remittances by NPI or by tax
          ID. If it routes by tax ID, everyone billing under yours is affected, not only you.
        </p>

        {(!billingNpi || !taxIdLast4) && (
          <p className="text-[12.5px] text-destructive" data-testid="cutover-identity-missing">
            Your practice identity is incomplete, so this would be filed under whatever is on file.
            Fill it in first.
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
            I know where this payer&rsquo;s remittances go today, and who needs to be told.
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
            {pending ? "Filing..." : "Move remittances to Pablo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
