// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo, useState } from "react"
import { SetupNav, SetupStepHead, SetupWizardShell } from "@/components/setup"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import { type PaymentRouteId, stepsForRoute } from "./routes"
import { STEP_BODIES } from "./stepBodies"

interface GetPaidWizardProps {
  /** Called after setup is marked done, so a host page can stop showing it. */
  onSettled?: () => void
}

/**
 * Setting up how a practice gets paid.
 *
 * One wizard for every therapist, because the facts underneath are shared: a
 * superbill and an insurance claim both need to know who she is and what she
 * charges. The first screen asks how she is paid TODAY and the rest follows
 * from the answer, so a private-pay practice never walks through insurance
 * screens and a paneled one is never asked to confirm an NPI she has held for
 * a decade.
 *
 * IT REMEMBERS WHERE SHE STOPPED. Her answer and the step she reached are
 * saved as she goes, so closing the tab mid-setup costs her nothing — she
 * comes back to the screen she left, on the branch she chose, not to the fork.
 * The step is stored by id rather than position: an index would point at the
 * wrong screen the first time a step is inserted ahead of it.
 *
 * "Finish later" marks setup settled, exactly as the calendar wizard does. A
 * first-visit surface she cannot leave is a trap, not a wizard; she gets a
 * card on the billing page instead and can return whenever.
 */
export function GetPaidWizard({ onSettled }: GetPaidWizardProps) {
  const { data: preferences } = usePreferences()
  const savePreferences = useSavePreferences()

  const savedRoute = (preferences?.billing_setup_route ?? null) as PaymentRouteId | null
  const [route, setRoute] = useState<PaymentRouteId | null>(null)
  const [stepId, setStepId] = useState<string | null>(null)

  // What she chose in this sitting wins over what was stored, so the screen
  // reacts immediately rather than waiting for the save to land.
  const activeRoute = route ?? savedRoute
  const steps = stepsForRoute(activeRoute)

  const activeStepId = stepId ?? preferences?.billing_setup_step ?? "route"
  // An unknown id — a step renamed or removed since she was last here — falls
  // back to the start of her branch rather than to a blank screen.
  const activeIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === activeStepId),
  )

  const remember = useCallback(
    (next: { route?: PaymentRouteId; step?: string; complete?: boolean }) => {
      if (!preferences) return
      savePreferences.mutate({
        ...preferences,
        ...(next.route !== undefined ? { billing_setup_route: next.route } : {}),
        ...(next.step !== undefined ? { billing_setup_step: next.step } : {}),
        ...(next.complete !== undefined ? { billing_setup_complete: next.complete } : {}),
      })
    },
    [preferences, savePreferences],
  )

  const goTo = useCallback(
    (index: number) => {
      const target = steps[index]
      if (!target) return
      setStepId(target.id)
      remember({ step: target.id })
    },
    [steps, remember],
  )

  function choose(id: PaymentRouteId) {
    setRoute(id)
    const next = stepsForRoute(id)[1]
    setStepId(next?.id ?? "route")
    remember({ route: id, step: next?.id ?? "route" })
  }

  const settle = useCallback(() => {
    remember({ complete: true })
    onSettled?.()
  }, [remember, onSettled])

  const current = useMemo(() => steps[activeIndex], [steps, activeIndex])
  const Body = STEP_BODIES[current?.id ?? "route"]
  const isLastStep = activeIndex === steps.length - 1

  return (
    <SetupWizardShell
      steps={steps}
      activeIndex={activeIndex}
      onJump={goTo}
      title="Getting paid"
      lede="A few questions, so this works the way your practice already does."
      onFinishLater={settle}
      aside={{
        img: "/pablo-tie.webp",
        caption: "Answer once. Pablo reuses it everywhere it's asked for.",
      }}
      footer={
        activeIndex > 0 ? (
          <SetupNav
            onBack={() => goTo(activeIndex - 1)}
            onContinue={isLastStep ? settle : () => goTo(activeIndex + 1)}
            // Never gated. Setup here is progressive by design — the same
            // stance ClaimsSetupChecklist takes, where a practice can fill in
            // what it has and come back for the rest. Blocking Continue until
            // a step is perfect would turn a resumable flow into a wall.
            canContinue
            isLastStep={isLastStep}
          />
        ) : undefined
      }
    >
      <Body route={activeRoute} onChoose={choose} />
    </SetupWizardShell>
  )
}
