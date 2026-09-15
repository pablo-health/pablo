// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepHead } from "@/components/setup"
import { Button } from "@/components/ui/button"
import { PaymentsSetup } from "./setupSlots.extensions"

/**
 * Connecting a processor, so an invoice can be paid by card.
 *
 * The panel itself comes from a slot, because which processor a deployment
 * uses is a per-deployment decision and the engine has no business naming one.
 * This screen owns everything around it: the framing, and the way out.
 *
 * THE WAY OUT IS OWNED HERE ON PURPOSE. A slot that rendered its own skip
 * control would read differently on this screen than on every other, and a
 * deployment could ship one that has no way out at all. Setup is resumable by
 * design — the same stance the rest of this wizard takes — so the step that
 * asks for the most commitment is the last place to start trapping people.
 *
 * Skipping says what it costs. "Skip for now" alone leaves her guessing which
 * part of her practice just stayed switched off, and she meets the consequence
 * later as a surprise — an invoice she cannot collect on. One sentence now is
 * cheaper than that, and it is the difference between deferring a thing
 * knowingly and not noticing it.
 */
export function PaymentsStep({
  onContinue,
  onBack,
}: {
  onContinue: () => void
  onBack: () => void
}) {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Card payments"
        title="Let clients pay by card"
        lede="Connect a payment processor and every invoice carries a payment link clients can pay from."
      />

      <PaymentsSetup />

      <div className="space-y-2 border-t border-border pt-4">
        <div className="flex items-center gap-3">
          <Button size="sm" onClick={onContinue}>
            Continue
          </Button>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto p-0"
            onClick={onBack}
          >
            Back
          </Button>
        </div>
        {/* Deliberately below the actions and in plain language: what is still
            true if she walks past this, said once, without arguing with her
            about it. */}
        <p className="text-[12.5px] text-muted-foreground" data-testid="payments-skip-note">
          You can set this up later in Settings. Until then, clients can&rsquo;t pay an invoice by
          card.
        </p>
      </div>
    </div>
  )
}
