// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"

interface SetupNavProps {
  /** Omit on the first step, where there's nothing to go back to. */
  onBack?: () => void
  /** Present only when the current step can be skipped. */
  onSkip?: () => void
  onContinue: () => void
  /** Disables Continue until the current step's gate is satisfied. */
  canContinue: boolean
  isLastStep: boolean
}

export function SetupNav({ onBack, onSkip, onContinue, canContinue, isLastStep }: SetupNavProps) {
  return (
    <div className="mt-6 flex items-center gap-2 border-t border-border pt-4">
      {onBack ? (
        <Button variant="ghost" size="sm" onClick={onBack}>
          Back
        </Button>
      ) : (
        <span />
      )}
      <span className="flex-1" />
      {onSkip ? (
        <Button variant="ghost" size="sm" onClick={onSkip}>
          Skip for now
        </Button>
      ) : null}
      <Button disabled={!canContinue} onClick={() => canContinue && onContinue()}>
        {isLastStep ? "Finish" : "Continue"}
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  )
}
