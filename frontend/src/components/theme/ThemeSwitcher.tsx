// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { THEMES, type ThemeId } from "@/lib/theme"
import { useTheme } from "./ThemeProvider"

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme()

  return (
    <div
      role="radiogroup"
      aria-label="Color theme"
      className="inline-flex items-center gap-1 rounded-full border border-neutral-300/70 bg-white/80 p-1 shadow-sm backdrop-blur"
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
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "bg-neutral-900 text-white"
                : "text-neutral-600 hover:bg-neutral-100"
            }`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
