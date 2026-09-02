// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ThemeSwitcher } from "@/components/theme/ThemeSwitcher"
import { ThemeFlavorNote } from "@/components/theme/ThemeFlavorNote"
import { CalendarDensitySettings } from "../CalendarDensitySettings"
import { SettingsCard } from "../ui"
import { useSettingsPreferences } from "../useSettingsPreferences"

/** You > Appearance. */
export function AppearancePage() {
  const { preferences, save, isSaving } = useSettingsPreferences()

  return (
    <>
      <SettingsCard title="Theme" description="Follows you between devices.">
        <ThemeSwitcher />
        <ThemeFlavorNote />
      </SettingsCard>

      {preferences && (
        <SettingsCard
          title="Calendar density"
          description="How much of the day fits on screen in week and day views."
        >
          <CalendarDensitySettings preferences={preferences} onSave={save} isSaving={isSaving} />
        </SettingsCard>
      )}
    </>
  )
}
