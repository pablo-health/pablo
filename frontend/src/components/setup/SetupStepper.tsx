// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check } from "lucide-react"

export interface SetupStepperStep {
  id: string
  label: string
}

interface SetupStepperProps {
  steps: readonly SetupStepperStep[]
  activeIndex: number
  onJump: (index: number) => void
  /** A step at or before `activeIndex` is always reachable; this decides
   * whether a step further along can be jumped to (e.g. once an earlier
   * gate has been satisfied). Defaults to "no" for every later step. */
  reachable?: (index: number) => boolean
}

export function SetupStepper({ steps, activeIndex, onJump, reachable }: SetupStepperProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {steps.map((step, i) => {
        const state = i < activeIndex ? "done" : i === activeIndex ? "current" : "todo"
        const canJump = i <= activeIndex || (reachable?.(i) ?? false)
        return (
          <button
            key={step.id}
            type="button"
            disabled={!canJump}
            onClick={() => canJump && onJump(i)}
            className={[
              "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
              state === "current"
                ? "bg-primary-100 text-primary-700"
                : state === "done"
                  ? "text-secondary-700 hover:bg-muted"
                  : "text-muted-foreground",
              !canJump ? "cursor-not-allowed opacity-60" : "",
            ].join(" ")}
          >
            <span
              className={[
                "flex h-5 w-5 items-center justify-center rounded-full text-[11px]",
                state === "current"
                  ? "bg-primary-600 text-white"
                  : state === "done"
                    ? "bg-secondary-500 text-white"
                    : "bg-muted text-muted-foreground",
              ].join(" ")}
            >
              {state === "done" ? <Check className="h-3 w-3" strokeWidth={3} /> : i + 1}
            </span>
            {step.label}
          </button>
        )
      })}
    </div>
  )
}
