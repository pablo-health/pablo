// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Check } from "lucide-react"
import { THEMES } from "@/lib/theme"
import { useTheme } from "./ThemeProvider"

// Compact theme picker for the header/avatar dropdown.
export function ThemeMenu() {
  const { theme, setTheme } = useTheme()
  return (
    <div className="border-b border-neutral-200 py-1">
      <div className="px-4 py-1 text-xs font-medium uppercase tracking-wide text-neutral-400">
        Theme
      </div>
      {THEMES.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => setTheme(t.id)}
          className="flex w-full items-center justify-between px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-50 transition-colors duration-150"
        >
          <span>{t.label}</span>
          {theme === t.id && <Check className="h-4 w-4 text-primary-600" />}
        </button>
      ))}
    </div>
  )
}
