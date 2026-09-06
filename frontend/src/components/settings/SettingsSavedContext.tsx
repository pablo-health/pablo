// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { createContext, useContext, useMemo } from "react"
import type { ReactNode } from "react"

interface SettingsSavedValue {
  saved: boolean
  flashSaved: () => void
}

/**
 * Lets a control anywhere on a settings page raise the header's "Saved" flash.
 *
 * Most settings save on change with no button, so this indicator is the only
 * feedback that the change landed. It lives in the page header, which is not a
 * parent of the control that triggers it, hence a context rather than a prop.
 */
const SettingsSavedContext = createContext<SettingsSavedValue>({
  saved: false,
  flashSaved: () => {},
})

export function SettingsSavedProvider({
  saved,
  flashSaved,
  children,
}: SettingsSavedValue & { children: ReactNode }) {
  const value = useMemo(() => ({ saved, flashSaved }), [saved, flashSaved])
  return <SettingsSavedContext.Provider value={value}>{children}</SettingsSavedContext.Provider>
}

export function useSettingsSaved(): SettingsSavedValue {
  return useContext(SettingsSavedContext)
}
