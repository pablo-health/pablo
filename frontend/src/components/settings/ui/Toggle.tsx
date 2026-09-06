// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { cn } from "@/lib/utils"

interface ToggleProps {
  checked: boolean
  onChange: (next: boolean) => void
  /** Required: the row label is not programmatically tied to this control. */
  label: string
  disabled?: boolean
  className?: string
}

/**
 * A binary switch. There is no shadcn `switch` in this project, which is why
 * this exists rather than wrapping one.
 */
export function Toggle({ checked, onChange, label, disabled, className }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-10 shrink-0 rounded-full transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        checked ? "bg-secondary-400" : "bg-foreground/20",
        disabled ? "cursor-default opacity-45" : "cursor-pointer",
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          "absolute left-[3px] top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  )
}
