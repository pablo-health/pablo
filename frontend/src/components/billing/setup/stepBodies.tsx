// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ComponentType } from "react"
import { SetupStepHead } from "@/components/setup"
import { RouteStep } from "./RouteStep"
import { BillingContactStep, PracticeIdentityStep, PrivatePayDoneStep, RatesStep } from "./SetupSteps"
import type { PaymentRouteId, StepId } from "./routes"

export interface StepBodyProps {
  route: PaymentRouteId | null
  onChoose: (id: PaymentRouteId) => void
}

/**
 * What each step renders.
 *
 * A table keyed by ``StepId`` rather than a chain of conditionals. The chain
 * mixed two dimensions — one arm read "this step AND this route" — so it grew
 * a branch every time a route gained its own ending. Worse, it let a step be
 * declared in ``routes.ts`` with no body here, and failed by quietly showing a
 * "not built yet" panel rather than by failing.
 *
 * Because ``StepId`` is a union, this is exhaustive in both directions: a step
 * added to the flow without a body will not compile, and a body for a step
 * that no longer exists will not either. That used to be a runtime test, which
 * only caught the gap once somebody walked the flow.
 *
 * Steps whose content differs by route switch inside their own body — see
 * ``DONE_BODIES``. Keeping this key one-dimensional is what stops the table
 * turning back into the chain it replaced.
 */
export const STEP_BODIES: Record<StepId, ComponentType<StepBodyProps>> = {
  route: ({ route, onChoose }) => <RouteStep selected={route} onChoose={onChoose} />,
  identity: () => <PracticeIdentityStep />,
  contact: () => <BillingContactStep />,
  rates: () => <RatesStep />,
  payers: () => <NotBuiltYet label="Payers" />,
  confirm: () => <NotBuiltYet label="What we found" />,
  record: () => <NotBuiltYet label="Your record" />,
  done: ({ route }) => <DoneStep route={route} />,
}

/**
 * How each route ends. Exhaustive over the routes for the same reason: every
 * branch should finish somewhere that says what happens next, and a route
 * added without an ending should be a compile error rather than a dead end
 * somebody finds later.
 */
const DONE_BODIES: Record<PaymentRouteId, ComponentType> = {
  private_pay: PrivatePayDoneStep,
  already_paneled: () => <NotBuiltYet label="Done" />,
  wants_panels: () => <NotBuiltYet label="Done" />,
  platform_to_own: () => <NotBuiltYet label="Done" />,
}

function DoneStep({ route }: { route: PaymentRouteId | null }) {
  const Body = route ? DONE_BODIES[route] : null
  return Body ? <Body /> : <NotBuiltYet label="Done" />
}

/**
 * A step with a place in the flow but no screen yet.
 *
 * It says so plainly rather than rendering an empty panel: a blank step reads
 * as something broken, and someone walking this flow should be able to tell
 * "not built" from "not working".
 */
function NotBuiltYet({ label }: { label: string }) {
  return (
    <div className="space-y-5">
      <SetupStepHead
        eyebrow="Coming next"
        title={label}
        lede="This step isn't built yet. Back returns you to the previous question."
      />
    </div>
  )
}
