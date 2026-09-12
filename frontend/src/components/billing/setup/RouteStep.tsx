// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepHead } from "@/components/setup"
import { PAYMENT_ROUTES, type PaymentRouteId } from "./routes"

/**
 * The only question every therapist answers.
 *
 * About today, not about ambition: the branch she lands on decides what the
 * rest of setup asks for, and a question about what she WANTS would send a
 * private-pay therapist who hopes to take insurance one day down a
 * credentialing path she is not ready for.
 */
export function RouteStep({
  selected,
  onChoose,
}: {
  selected: PaymentRouteId | null
  onChoose: (id: PaymentRouteId) => void
}) {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Step 1"
        title="How do you get paid today?"
        lede="Choose what best describes your practice right now. You can change this later."
      />

      <div className="space-y-2">
        {PAYMENT_ROUTES.map((option) => {
          const isSelected = selected === option.id
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onChoose(option.id)}
              aria-pressed={isSelected}
              className={`w-full rounded-xl border p-4 text-left transition-colors ${
                isSelected
                  ? "border-neutral-900 bg-neutral-50"
                  : "border-border bg-card hover:border-neutral-400 hover:bg-neutral-50"
              }`}
            >
              <span className="block text-sm font-medium text-neutral-900">{option.label}</span>
              <span className="mt-1 block text-sm text-muted-foreground">{option.detail}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
