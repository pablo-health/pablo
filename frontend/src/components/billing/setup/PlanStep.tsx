// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepHead } from "@/components/setup"
import { Button } from "@/components/ui/button"
import type { CurrentStateId } from "./routes"

/**
 * What each tick means we are about to set up, in her terms rather than ours.
 *
 * Named per tick because a line true of every practice tells her nothing about
 * her own.
 */
const PLAN_LINES: Record<CurrentStateId, string> = {
  self_pay: "Card and bank payments from clients, and superbills when they need them",
  platform: "Your own practice details, kept separate from the service that pays you",
  own_insurance: "Claims to the insurers you're already in-network with",
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
  onToggleCredentialing,
  onBack,
  onContinue,
}: {
  selected: readonly CurrentStateId[]
  wantsCredentialing: boolean
  onToggleCredentialing: (next: boolean) => void
  onBack: () => void
  onContinue: () => void
}) {
  const onPlatform = selected.includes("platform")

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 2"
        title="Here's what we'll set up"
        lede="Based on what you just told us. Nothing is final — you can change any of it later."
      />

      <ul className="space-y-2 text-sm text-neutral-700" data-testid="plan-lines">
        {selected.map((id) => (
          <li key={id} className="rounded-lg border border-border bg-card p-3">
            {PLAN_LINES[id]}
          </li>
        ))}
      </ul>

      {/* Said plainly, and only to someone it applies to. The reassurance is
          the point: the commonest fear about pointing a second system at your
          billing is that it will quietly start rerouting money. */}
      {onPlatform && (
        <p className="text-[12.5px] text-muted-foreground" data-testid="platform-untouched">
          Nothing here changes how the service that pays you works today. We won&rsquo;t enrol you
          with payers, redirect any payment, or ask for anything only an independent biller needs.
        </p>
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
            I also want to take insurance under my own contracts
          </span>
          <span className="mt-1 block text-sm text-muted-foreground">
            Pablo will help you apply and keep track of it. It takes months, nothing starts until
            you&rsquo;re ready, and none of it changes how you&rsquo;re paid in the meantime.
          </span>
        </span>
      </label>

      <div className="flex items-center gap-3 border-t border-border pt-4">
        <Button size="sm" onClick={onContinue}>
          Set up billing
        </Button>
        <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={onBack}>
          Something missing? Go back.
        </Button>
      </div>
    </div>
  )
}
