// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef } from "react"
import { useAuth } from "@/lib/auth-context"
import { getPreferences, saveThemePreference } from "@/lib/api/users"
import { isThemeId, type ThemeId } from "@/lib/theme"
import { useTheme } from "./ThemeProvider"

/**
 * Bridges the local (localStorage) theme with the signed-in account.
 * Mounted inside the authenticated app: adopts the account's saved theme
 * on load and persists user-initiated changes. Best-effort — the login
 * screen works on localStorage alone, before any account is known.
 */
export function ThemeSync() {
  const { user } = useAuth()
  const { theme, setTheme } = useTheme()
  const lastPersisted = useRef<ThemeId | null>(null)

  useEffect(() => {
    if (!user) return
    let cancelled = false
    getPreferences()
      .then((prefs) => {
        if (cancelled) return
        if (isThemeId(prefs.theme)) {
          lastPersisted.current = prefs.theme
          setTheme(prefs.theme)
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [user, setTheme])

  useEffect(() => {
    if (!user || lastPersisted.current === null) return
    if (lastPersisted.current === theme) return
    lastPersisted.current = theme
    saveThemePreference(theme).catch(() => {})
  }, [user, theme])

  return null
}
