// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

interface SetupStepHeadProps {
  eyebrow: string
  title: string
  lede: string
}

/** Eyebrow / title / lede header for a single wizard step's body. */
export function SetupStepHead({ eyebrow, title, lede }: SetupStepHeadProps) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium uppercase tracking-wide text-primary-600">{eyebrow}</p>
      <h2 className="font-display text-xl font-semibold text-neutral-900">{title}</h2>
      <p className="text-sm text-muted-foreground">{lede}</p>
    </div>
  )
}
