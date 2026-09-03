// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { THEMES, type ThemeId } from "@/lib/theme"
import { useTheme } from "./ThemeProvider"
import { cn } from "@/lib/utils"

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme()

  return (
    <div
      role="radiogroup"
      aria-label="Color theme"
      className="flex flex-wrap items-center justify-center gap-1 rounded-2xl border border-neutral-300/70 bg-card/80 p-1 shadow-sm backdrop-blur"
    >
      {THEMES.map((t) => {
        const active = t.id === theme
        return (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={active}
            title={t.description}
            onClick={() => setTheme(t.id as ThemeId)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-foreground/5"
            )}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
