// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo } from "react"
import { SetupNav, SetupWizardShell } from "@/components/setup"
import { Skeleton } from "@/components/ui/skeleton"
import { usePreferences, useSavePreferences } from "@/hooks/usePreferences"
import { type CurrentStateId, stepsForState } from "./routes"
import { STEP_BODIES } from "./stepBodies"
import { useBillingSetupDraft } from "./useBillingSetupDraft"

interface GetPaidWizardProps {
  /** Called after setup is marked done, so a host page can stop showing it. */
  onSettled?: () => void
}

/**
 * Setting up how a practice gets paid.
 *
 * One wizard for every therapist, because the facts underneath are shared: a
 * superbill and an insurance claim both need to know who she is and what she
 * charges.
 *
 * The first screen is a CHECKLIST, not a fork. It asks what is true of how she
 * is paid today, several answers allowed, because several are true at once — a
 * therapist on Headway who also sees a few clients privately is an ordinary
 * practice, and the single-select version this replaced could not let her say
 * so. What she WANTS is asked once on the plan screen, separately, so wanting
 * insurance one day never drags a private-pay practice through screens she is
 * not ready for.
 *
 * EVERY ANSWER ONLY ADDS. Nothing she ticks removes a step or sends her down a
 * different path, which is what makes the checklist safe to under-answer — and
 * people do under-answer, ticking what feels most true and moving on. A missed
 * tick costs her a card in Billing later, never a wrong path.
 *
 * IT REMEMBERS WHERE SHE STOPPED. Her answers and the step she reached are
 * saved as she goes, so closing the tab mid-setup costs her nothing. The step
 * is stored by id rather than position: an index would point at the wrong
 * screen the first time a step is inserted ahead of it.
 *
 * "Finish later" marks setup settled, exactly as the calendar wizard does. A
 * first-visit surface she cannot leave is a trap, not a wizard; she gets a
 * card on the billing page instead and can return whenever.
 */
export function GetPaidWizard({ onSettled }: GetPaidWizardProps) {
  const { data: preferences } = usePreferences()
  const savePreferences = useSavePreferences()

  // What she has done in this sitting laid over what was stored, so the screen
  // reacts immediately rather than waiting for the save to land. The merge and
  // the one default it applies live in `resolveAnswers`, which is pure.
  const { answers, apply, settle: persistSettled } = useBillingSetupDraft(
    preferences,
    savePreferences.mutate,
  )

  const steps = stepsForState(answers.state, answers.wantsCredentialing, answers.wantsCardPayments)

  // An unknown id — a step renamed, or one she no longer walks because she
  // unticked what added it — falls back to the start rather than a blank
  // screen.
  const activeIndex = Math.max(
    0,
    steps.findIndex((step) => step.id === answers.step),
  )

  const goTo = useCallback(
    (index: number) => {
      const target = steps[index]
      if (!target) return
      apply({ type: "goTo", step: target.id })
    },
    [steps, apply],
  )

  const toggle = useCallback(
    (id: CurrentStateId) => apply({ type: "toggleState", id }),
    [apply],
  )

  const confirmChecklist = useCallback(
    () => apply({ type: "answerChecklist", state: answers.state ?? [] }),
    [apply, answers.state],
  )

  // Her real answer, not an absence of one. An empty list is what "not seeing
  // clients yet" means, and it must never read back as "has not answered" —
  // which is exactly why the stored value is a list that can be empty rather
  // than a nullable route.
  const noClientsYet = useCallback(
    () => apply({ type: "answerChecklist", state: [] }),
    [apply],
  )

  const toggleCredentialing = useCallback(
    (value: boolean) => apply({ type: "wantsCredentialing", value }),
    [apply],
  )

  // Written on every toggle, including when she turns it OFF. The default in
  // `resolveAnswers` only applies while she has no stored opinion, so a
  // self-pay clinician who unticks this must leave a `false` behind —
  // otherwise the default re-ticks it next time and the step she just declined
  // comes back.
  const toggleCardPayments = useCallback(
    (value: boolean) => apply({ type: "wantsCardPayments", value }),
    [apply],
  )

  const settle = useCallback(() => {
    persistSettled()
    onSettled?.()
  }, [persistSettled, onSettled])

  const current = useMemo(() => steps[activeIndex], [steps, activeIndex])
  const Body = STEP_BODIES[current?.id ?? "route"]
  const isLastStep = activeIndex === steps.length - 1
  // The first two screens carry their own buttons — the checklist gates
  // Continue until she has answered, and the plan screen's primary action says
  // what it does ("Set up billing") rather than "Continue".
  const ownsItsNav = current?.id === "route" || current?.id === "plan"

  // Nothing is interactive until her saved answers are in hand. `remember`
  // cannot write without them, so a click landing first would be accepted on
  // screen and silently not saved — she would answer, move on, and meet the
  // checklist again next time. Waiting is the honest version of that.
  if (!preferences) {
    return (
      <div className="space-y-4" data-testid="wizard-loading">
        <Skeleton className="h-12 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

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
        caption: current?.caption ?? "Tell Pablo once. He'll take it from here.",
      }}
      footer={
        !ownsItsNav ? (
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
      <Body
        selected={answers.state ?? []}
        wantsCredentialing={answers.wantsCredentialing}
        wantsCardPayments={answers.wantsCardPayments}
        onToggle={toggle}
        onToggleCredentialing={toggleCredentialing}
        onToggleCardPayments={toggleCardPayments}
        onContinue={current?.id === "route" ? confirmChecklist : () => goTo(activeIndex + 1)}
        onBack={() => goTo(activeIndex - 1)}
        onNoClients={noClientsYet}
      />
    </SetupWizardShell>
  )
}
