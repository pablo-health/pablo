// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { createContext, useCallback, useContext, useState } from "react"
import {
  DEFAULT_THEME,
  THEME_STORAGE_KEY,
  isThemeId,
  type ThemeId,
} from "@/lib/theme"

interface ThemeContextValue {
  theme: ThemeId
  setTheme: (theme: ThemeId) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

// The no-FOUC script in the root layout already resolved the theme from
// localStorage and applied it to <html> before paint — adopt that value.
function readInitialTheme(): ThemeId {
  if (typeof document !== "undefined") {
    const attr = document.documentElement.dataset.theme
    if (isThemeId(attr)) return attr
  }
  return DEFAULT_THEME
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readInitialTheme)

  const setTheme = useCallback((next: ThemeId) => {
    setThemeState(next)
    localStorage.setItem(THEME_STORAGE_KEY, next)
    document.documentElement.dataset.theme = next
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error("useTheme must be used within a ThemeProvider")
  return ctx
}
