// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepHead } from "@/components/setup"
import { Button } from "@/components/ui/button"
import type { CurrentStateId } from "./routes"
import { HAS_PAYMENTS_SETUP } from "./setupSlots.extensions"

/**
 * What each tick means we are about to set up, in her terms rather than ours.
 *
 * Named per tick because a line true of every practice tells her nothing about
 * her own.
 */
const PLAN_LINES: Record<CurrentStateId, string> = {
  self_pay: "Payments from clients, plus superbills when a client needs one",
  platform: "Billing for work you do outside the service",
  own_insurance: "Claims for contracts you already have in your own name",
}

/**
 * Screen 2: a confirmation, not a question.
 *
 * She would want to see this anyway, which is the whole reason it can carry the
 * credentialing ask without costing a screen. It is also her second look at the
 * checklist — under-answering screen 1 is cheap precisely because this page
 * shows her what that answer bought, while Back is still one click away.
 *
 * The credentialing row is default off and stays off. Nothing here is
 * separately charged, so there is no reason to pre-tick it, and a pre-ticked
 * box would claim she asked for something she did not. It also enables nothing
 * by itself: it adds the screens that collect what an application needs, and
 * the real offer comes later.
 */
export function PlanStep({
  selected,
  wantsCredentialing,
  wantsCardPayments,
  onToggleCredentialing,
  onToggleCardPayments,
  onBack,
  onContinue,
}: {
  selected: readonly CurrentStateId[]
  wantsCredentialing: boolean
  wantsCardPayments: boolean
  onToggleCredentialing: (next: boolean) => void
  onToggleCardPayments: (next: boolean) => void
  onBack: () => void
  onContinue: () => void
}) {
  const onPlatform = selected.includes("platform")

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 2"
        title="What Pablo will help you set up"
        lede="Based on what you chose. You can change this later."
      />

      <ul className="space-y-2 text-sm text-neutral-700" data-testid="plan-lines">
        {selected.map((id) => (
          <li key={id} className="rounded-lg border border-border bg-card p-3">
            {PLAN_LINES[id]}
          </li>
        ))}
      </ul>

      {/* One sentence, and deliberately not a list.
          It used to name payer enrollment, payment redirects and billing
          identifiers — three things nothing on this screen touches. Reciting
          what will NOT happen introduces machinery the reader had not thought about
          and makes the safe default sound dangerous. */}
      {onPlatform && (
        <p className="text-[12.5px] text-muted-foreground" data-testid="platform-untouched">
          This won&rsquo;t change how the service handles your current clients or payments.
        </p>
      )}

      {/* Offered wherever a deployment can actually connect a processor. When
          it cannot, the row is absent rather than present-and-inert: a tick
          that leads to a blank screen is worse than never being asked.

          UNLIKE the credentialing row below, this one arrives ticked for a
          self-pay practice — and that is not the pre-ticking the comment below
          argues against. Screen 1's self-pay option reads "Card, cash, bank
          transfer"; a clinician who chose it HAS said she takes card, so the
          tick carries her own answer forward. Nothing on screen 1 implies
          credentialing, which is why pre-ticking that one would be inventing
          an answer rather than reading one. */}
      {HAS_PAYMENTS_SETUP && (
        <label className="flex cursor-pointer gap-3 rounded-xl border border-border bg-card p-4">
          <input
            type="checkbox"
            checked={wantsCardPayments}
            onChange={(e) => onToggleCardPayments(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 rounded-[3px] border-neutral-400 accent-neutral-900"
            data-testid="wants-card-payments"
          />
          <span>
            <span className="block text-sm font-medium text-neutral-900">
              I want clients to be able to pay by card
            </span>
            <span className="mt-1 block text-sm text-muted-foreground">
              Connect a payment processor and every invoice carries a payment link. You can set
              this up now or later.
            </span>
          </span>
        </label>
      )}

      <label className="flex cursor-pointer gap-3 rounded-xl border border-border bg-card p-4">
        <input
          type="checkbox"
          checked={wantsCredentialing}
          onChange={(e) => onToggleCredentialing(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 rounded-[3px] border-neutral-400 accent-neutral-900"
          data-testid="wants-credentialing"
        />
        <span>
          <span className="block text-sm font-medium text-neutral-900">
            I want to apply for my own insurance contracts
          </span>
          {/* "Nothing starts until you're ready" was reassuring in tone and
              unclear about what "starts" — so it said nothing and sounded like
              it said something. What matters is that the service keeps
              working, which this says outright. */}
          <span className="mt-1 block text-sm text-muted-foreground">
            Pablo can help prepare and track your applications while you keep using the service.
          </span>
        </span>
      </label>

      <div className="flex items-center gap-3 border-t border-border pt-4">
        {/* Not "Set up billing". Some people reach this screen only to prepare
            a credentialing record, and naming the button after the other
            person's goal tells them they are in the wrong place. */}
        <Button size="sm" onClick={onContinue}>
          Continue
        </Button>
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={onBack}>
          Go back
        </Button>
      </div>
    </div>
  )
}
