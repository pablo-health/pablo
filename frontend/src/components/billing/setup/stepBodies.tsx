// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ComponentType } from "react"
import { NpiLookupStep } from "@/components/credentialing/NpiLookupStep"
import { DoneStep } from "./DoneStep"
import { PlanStep } from "./PlanStep"
import { RouteStep } from "./RouteStep"
import {
  BillingContactStep,
  CredentialingRecordStep,
  PayersStep,
  PracticeIdentityStep,
  RatesStep,
} from "./SetupSteps"
import type { CurrentStateId, StepId } from "./routes"

export interface StepBodyProps {
  selected: readonly CurrentStateId[]
  wantsCredentialing: boolean
  onToggle: (id: CurrentStateId) => void
  onToggleCredentialing: (next: boolean) => void
  onContinue: () => void
  onBack: () => void
  onNoClients: () => void
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
 * There is no longer a second table of endings. ``DoneStep`` composes itself
 * from what she ticked, because she can be several things at once and a table
 * keyed by one of them cannot say so.
 */
export const STEP_BODIES: Record<StepId, ComponentType<StepBodyProps>> = {
  route: ({ selected, onToggle, onContinue, onNoClients }) => (
    <RouteStep
      selected={selected}
      onToggle={onToggle}
      onContinue={onContinue}
      onNoClients={onNoClients}
    />
  ),
  plan: ({ selected, wantsCredentialing, onToggleCredentialing, onBack, onContinue }) => (
    <PlanStep
      selected={selected}
      wantsCredentialing={wantsCredentialing}
      onToggleCredentialing={onToggleCredentialing}
      onBack={onBack}
      onContinue={onContinue}
    />
  ),
  identity: () => <PracticeIdentityStep />,
  contact: () => <BillingContactStep />,
  rates: () => <RatesStep />,
  payers: () => <PayersStep />,
  confirm: () => <NpiLookupStep />,
  record: () => <CredentialingRecordStep />,
  done: ({ selected, wantsCredentialing }) => (
    <DoneStep selected={selected} wantsCredentialing={wantsCredentialing} />
  ),
}
