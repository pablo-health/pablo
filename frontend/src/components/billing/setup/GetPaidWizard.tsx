// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useMemo } from "react"
import { SetupNav, StepSaveProvider, SetupWizardShell, useStepSave } from "@/components/setup"
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
  // The provider has to be ABOVE the wizard, not inside it, because the wizard
  // itself is what reads the registered saves. See `StepSave`.
  return (
    <StepSaveProvider>
      <GetPaidWizardBody onSettled={onSettled} />
    </StepSaveProvider>
  )
}

function GetPaidWizardBody({ onSettled }: GetPaidWizardProps) {
  const { data: preferences } = usePreferences()
  const savePreferences = useSavePreferences()
  // Commits whatever form is standing on the current step. The steps that
  // collect facts mount the settings card for those facts, and that card saves
  // on its own button — so without this, Continue walked past everything just
  // typed and it was gone.
  const saveStep = useStepSave()

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

  /**
   * Leave the current step, having first committed what is on it.
   *
   * A rejected save keeps her here. The card has already put the reason on
   * screen next to the field it belongs to, so there is nothing to add — and
   * moving on would mean the ending speaks for a tax id the server refused.
   */
  const commitAndGoTo = useCallback(
    async (index: number) => {
      try {
        await saveStep()
      } catch {
        return
      }
      goTo(index)
    },
    [saveStep, goTo],
  )

  /**
   * Leave without being held up by a failure — going back, and finishing
   * later.
   *
   * Still saves, because typing something and pressing Back should not throw
   * it away either. But neither of these is a claim that the step is good, and
   * a wizard that will not let her leave is the trap this flow is built to
   * avoid.
   */
  const commitAndLeave = useCallback(
    async (leave: () => void) => {
      await saveStep().catch(() => {})
      leave()
    },
    [saveStep],
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

  // Finishing is still leaving a step, and the last one collects facts on
  // several routes. Settling without committing it would lose the last screen
  // she filled in — the one she was most likely still typing on.
  const finish = useCallback(() => void commitAndLeave(settle), [commitAndLeave, settle])

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
      onJump={(index) => void commitAndGoTo(index)}
      title="Getting paid"
      lede="A few questions, so this works the way your practice already does."
      onFinishLater={finish}
      aside={{
        img: "/pablo-tie.webp",
        caption: current?.caption ?? "Tell Pablo once. He'll take it from here.",
      }}
      footer={
        !ownsItsNav ? (
          <SetupNav
            onBack={() => void commitAndLeave(() => goTo(activeIndex - 1))}
            onContinue={isLastStep ? finish : () => void commitAndGoTo(activeIndex + 1)}
            // Never gated. Setup here is progressive by design — the same
            // stance ClaimsSetupChecklist takes, where a practice can fill in
            // what it has and come back for the rest. Blocking Continue until
            // a step is perfect would turn a resumable flow into a wall.
            //
            // Committing the step is not the same as gating it: an empty form
            // saves nothing and Continue behaves exactly as before. Only a
            // form she filled in badly holds her, and only because the field
            // beside her is showing why.
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
        onContinue={
          current?.id === "route" ? confirmChecklist : () => void commitAndGoTo(activeIndex + 1)
        }
        onBack={() => void commitAndLeave(() => goTo(activeIndex - 1))}
        onNoClients={noClientsYet}
      />
    </SetupWizardShell>
  )
}
