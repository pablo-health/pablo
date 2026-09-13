// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepHead } from "@/components/setup"
import { Button } from "@/components/ui/button"
import { CURRENT_STATES, type CurrentStateId } from "./routes"

/**
 * The only question every therapist answers, and it is a checklist.
 *
 * About today, not about ambition: a question about what she WANTS would send
 * a privately-paid therapist who hopes to take insurance one day down a
 * credentialing path she is not ready for. What she wants is asked once, on the
 * next screen, where it costs nothing.
 *
 * Several answers at once, because several are true at once. A therapist on
 * Headway who also sees a few clients privately could not describe herself
 * here when this was a single choice, and the product quietly decided for her.
 *
 * Squares, not cards that look like radios: the affordance has to say "several"
 * before she reads a word. And the reminder to pick everything sits BESIDE the
 * continue button as well as in the subtitle, because the button area is what
 * she reads last.
 */
export function RouteStep({
  selected,
  onToggle,
  onContinue,
  onNoClients,
}: {
  selected: readonly CurrentStateId[]
  onToggle: (id: CurrentStateId) => void
  onContinue: () => void
  onNoClients: () => void
}) {
  const nothingPicked = selected.length === 0

  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 1"
        title="How do clients pay you today?"
        lede="Pick everything that applies. This just decides what we set up first — you can add the rest any time."
      />

      <div className="space-y-2">
        {CURRENT_STATES.map((option) => {
          const isSelected = selected.includes(option.id)
          return (
            <label
              key={option.id}
              className={`flex w-full cursor-pointer gap-3 rounded-xl border p-4 text-left transition-colors ${
                isSelected
                  ? "border-neutral-900 bg-neutral-50"
                  : "border-border bg-card hover:border-neutral-400 hover:bg-neutral-50"
              }`}
            >
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggle(option.id)}
                className="mt-0.5 h-4 w-4 shrink-0 rounded-[3px] border-neutral-400 accent-neutral-900"
              />
              <span>
                <span className="block text-sm font-medium text-neutral-900">{option.label}</span>
                <span className="mt-1 block text-sm text-muted-foreground">{option.detail}</span>
              </span>
            </label>
          )
        })}
      </div>

      {/* A link rather than a fourth box. It contradicts the other three, and a
          checkbox that unticks its siblings is a trap — disabling them is worse.
          As a link the exclusivity is structural and needs no enforcing. */}
      <p className="text-[12.5px] text-muted-foreground">
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto p-0 text-[12.5px] underline underline-offset-4"
          onClick={onNoClients}
        >
          I&rsquo;m not seeing clients yet
        </Button>
      </p>

      <div className="flex items-center gap-3 border-t border-border pt-4">
        <Button size="sm" onClick={onContinue} disabled={nothingPicked}>
          Continue
        </Button>
        <span className="text-[12.5px] text-muted-foreground">
          {nothingPicked
            ? "Pick at least one, or tell us you're not seeing clients yet."
            : "Checked everything that applies?"}
        </span>
      </div>
    </div>
  )
}
