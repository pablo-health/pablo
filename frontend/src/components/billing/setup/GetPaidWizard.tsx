// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { SetupNav, SetupStepHead, SetupWizardShell } from "@/components/setup"
import { PAYMENT_ROUTES, type PaymentRouteId, stepsForRoute } from "./routes"

interface GetPaidWizardProps {
  /** Called when she leaves setup unfinished. */
  onFinishLater?: () => void
}

/**
 * Setting up how a practice gets paid.
 *
 * One wizard for every therapist, because the facts underneath are shared: a
 * superbill and an insurance claim both need to know who she is and what she
 * charges. The first screen asks how she is paid TODAY and the rest of the
 * wizard follows from the answer, so a private-pay practice never walks
 * through insurance screens and a paneled one is never asked to confirm an NPI
 * she has held for a decade.
 *
 * Nothing here asks for something the record can answer. The steps she has
 * already satisfied elsewhere — in settings, at onboarding, through an import
 * — are skipped rather than re-asked.
 */
export function GetPaidWizard({ onFinishLater }: GetPaidWizardProps) {
  const [route, setRoute] = useState<PaymentRouteId | null>(null)
  const [stepIndex, setStepIndex] = useState(0)

  const steps = stepsForRoute(route)

  function choose(id: PaymentRouteId) {
    setRoute(id)
    setStepIndex(1)
  }

  return (
    <SetupWizardShell
      steps={steps}
      activeIndex={stepIndex}
      onJump={setStepIndex}
      title="Getting paid"
      lede="A few questions, so this works the way your practice already does."
      onFinishLater={onFinishLater}
      aside={{
        img: "/pablo-tie.webp",
        caption: "Answer once. Pablo reuses it everywhere it's asked for.",
      }}
      footer={
        stepIndex > 0 ? (
          <SetupNav
            onBack={() => setStepIndex(stepIndex - 1)}
            onContinue={() => setStepIndex(stepIndex + 1)}
            canContinue={false}
            isLastStep={false}
          />
        ) : undefined
      }
    >
      {stepIndex === 0 ? (
        <RouteStep selected={route} onChoose={choose} />
      ) : (
        <NextStepPlaceholder label={steps[stepIndex]?.label ?? "Next"} />
      )}
    </SetupWizardShell>
  )
}

function RouteStep({
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
              <span className="block text-sm font-medium text-neutral-900">
                {option.label}
              </span>
              <span className="mt-1 block text-sm text-muted-foreground">
                {option.detail}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/**
 * Placeholder for a step that is not built yet.
 *
 * It says so plainly rather than rendering an empty panel: a blank step reads
 * as something broken, and the person reviewing this flow should be able to
 * tell the difference between "not built" and "not working".
 */
function NextStepPlaceholder({ label }: { label: string }) {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Coming next"
        title={label}
        lede="This step isn't built yet. Back returns you to the first question."
      />
    </div>
  )
}
