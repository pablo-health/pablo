// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { ReactNode } from "react"

export interface CardOption<T extends string> {
  value: T
  label: ReactNode
  /** The sentence that makes the choice decidable. This is why it is a card. */
  hint: ReactNode
}

interface OptionCardsProps<T extends string> {
  value: T
  onChange: (next: T) => void
  options: CardOption<T>[]
  label: string
  columns?: 2 | 3 | 4
}

/** For choices that need a sentence to decide, not just a word. */
export function OptionCards<T extends string>({
  value,
  onChange,
  options,
  label,
  columns = 3,
}: OptionCardsProps<T>) {
  const grid = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns]

  return (
    <div role="radiogroup" aria-label={label} className={`grid grid-cols-1 gap-2.5 ${grid}`}>
      {options.map((option) => {
        const selected = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={[
              "rounded-xl border-[1.5px] bg-card px-3.5 py-3 text-left transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected
                ? "border-foreground bg-foreground/[0.03] ring-1 ring-inset ring-foreground"
                : "border-border hover:border-muted-foreground",
            ].join(" ")}
          >
            <span className="block text-[13.5px] font-semibold text-foreground">{option.label}</span>
            <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{option.hint}</span>
          </button>
        )
      })}
    </div>
  )
}
