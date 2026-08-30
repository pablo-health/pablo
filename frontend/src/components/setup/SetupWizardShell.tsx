// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SetupStepper, type SetupStepperStep } from "@/components/setup/SetupStepper"
import { SetupAside } from "@/components/setup/SetupAside"

interface SetupWizardShellProps {
  steps: readonly SetupStepperStep[]
  activeIndex: number
  onJump: (index: number) => void
  title: string
  lede: string
  /** Renders a "Finish later" link in the header when provided. Omit it
   * to hide the link — the shell has no opinion on when that should be
   * possible, that's the consumer's gate to decide. */
  onFinishLater?: () => void
  aside?: { img: string; caption: string }
  /** Passed through to the stepper; see `SetupStepper`'s `reachable` prop. */
  reachable?: (index: number) => boolean
  /** Typically a `SetupNav`. Rendered below the step body inside the panel. */
  footer?: React.ReactNode
  children: React.ReactNode
}

/**
 * Chrome for a multi-step, in-app setup wizard: header, stepper, an
 * optional side illustration, and a panel that holds the active step's
 * body plus its footer. Purely presentational — the consumer owns
 * `activeIndex`, every step's gate, and any persistence.
 */
export function SetupWizardShell({
  steps,
  activeIndex,
  onJump,
  title,
  lede,
  onFinishLater,
  aside,
  reachable,
  footer,
  children,
}: SetupWizardShellProps) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold text-neutral-900">{title}</h1>
          <p className="text-sm text-muted-foreground">{lede}</p>
        </div>
        {onFinishLater ? (
          <button
            type="button"
            onClick={onFinishLater}
            className="ml-auto text-sm font-medium text-muted-foreground hover:text-neutral-700"
          >
            Finish later
          </button>
        ) : null}
      </div>

      {steps.length > 0 ? (
        <SetupStepper steps={steps} activeIndex={activeIndex} onJump={onJump} reachable={reachable} />
      ) : null}

      <div className={aside ? "grid gap-5 md:grid-cols-[200px_1fr]" : "grid gap-5"}>
        {aside ? <SetupAside img={aside.img} caption={aside.caption} /> : null}
        <div className="rounded-2xl border border-border bg-card p-6 shadow-sm">
          {children}
          {footer}
        </div>
      </div>
    </div>
  )
}
