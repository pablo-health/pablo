// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Wizard chrome for an onboarding step: a centered card with a header
 * (optional "Step N of M" progress row, title, description) and the
 * step's content as children.
 *
 * The progress row only renders when the active surface has more than
 * one numbered step — a single-step surface (the stock minimal
 * second-factor gate) shows just the title. A downstream build that
 * ships a multi-step guided setup can shadow this module to add its own
 * artwork/branding; the props are the stable contract every step page
 * renders against.
 */

import { useEffect } from "react"
import type { StepId } from "@/lib/analytics/types"
import { trackOnboardingStepViewed } from "@/lib/analytics/onboarding"
import { getOnboardingSurface } from "@/lib/onboarding/surface"
import { requiredStepPosition } from "@/lib/onboarding/types"

interface OnboardingStepShellProps {
  stepId: StepId
  title: string
  description?: string
  /** Override the computed eyebrow (e.g. a plain label for a step that
   * isn't a numbered required step). */
  eyebrow?: string
  /** Accepted for cross-build prop compatibility; the stock shell has
   * no side panel to omit. */
  noAside?: boolean
  children: React.ReactNode
}

export function OnboardingStepShell({
  stepId,
  title,
  description,
  eyebrow,
  children,
}: OnboardingStepShellProps) {
  useEffect(() => {
    trackOnboardingStepViewed(stepId)
  }, [stepId])

  const pos = requiredStepPosition(getOnboardingSurface(), stepId)
  const showProgress = pos !== null && pos.total > 1
  const stepLabel = pos ? `Step ${pos.index}${pos.subLabel ?? ""} of ${pos.total}` : null

  return (
    <div className="w-full max-w-2xl bg-white rounded-2xl shadow-xl overflow-hidden p-8 md:p-10">
      <div className="space-y-6">
        <header className="space-y-1">
          {showProgress ? (
            <div className="flex items-center gap-3 mb-1" aria-label={stepLabel ?? ""}>
              <span
                className="text-xs font-semibold whitespace-nowrap tracking-wide"
                style={{ color: "var(--color-neutral-500)" }}
              >
                {stepLabel}
              </span>
              <div
                className="flex-1 h-1.5 rounded-full overflow-hidden"
                style={{ background: "var(--color-neutral-200)" }}
              >
                <div
                  className="h-full rounded-full transition-[width] duration-500 ease-out"
                  style={{
                    width: `${(pos?.fraction ?? 0) * 100}%`,
                    background: "var(--action-bg)",
                  }}
                />
              </div>
            </div>
          ) : eyebrow ? (
            <p className="text-sm font-semibold mb-1" style={{ color: "var(--color-primary-600)" }}>
              {eyebrow}
            </p>
          ) : null}
          <h1 className="text-2xl md:text-3xl font-display font-semibold text-neutral-900">
            {title}
          </h1>
          {description && <p className="text-neutral-600">{description}</p>}
        </header>
        {children}
      </div>
    </div>
  )
}
