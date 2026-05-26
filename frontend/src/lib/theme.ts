// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

export const THEMES = [
  {
    id: "warm-paper",
    label: "Pablo",
    description: "The Pablo look — crisp near-white with honey accents.",
  },
  {
    id: "dark",
    label: "Dark",
    description: "Easy on the eyes in low light.",
  },
  {
    id: "high-contrast",
    label: "High Contrast",
    description: "Maximum legibility.",
  },
  {
    id: "boring-ehr",
    label: "Boring EHR 🙂",
    description: "The anti-goal, made selectable. You have been warned.",
  },
] as const

export type ThemeId = (typeof THEMES)[number]["id"]

const THEME_IDS: readonly string[] = THEMES.map((t) => t.id)

export const THEME_STORAGE_KEY = "pablo-theme"

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return !!value && THEME_IDS.includes(value)
}

const envDefault = process.env.NEXT_PUBLIC_DEFAULT_THEME
export const DEFAULT_THEME: ThemeId = isThemeId(envDefault)
  ? envDefault
  : "warm-paper"
