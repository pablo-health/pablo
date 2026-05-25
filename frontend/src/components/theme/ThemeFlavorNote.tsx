// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useTheme } from "./ThemeProvider"

// A warm word from Pablo for the one theme that has no warmth of its own.
export function ThemeFlavorNote() {
  const { theme } = useTheme()
  if (theme !== "boring-ehr") return null
  return (
    <p className="mt-3 text-sm text-neutral-600">
      <span className="font-medium text-neutral-800">Pablo:</span> Bold choice.
      I&rsquo;ll keep the kettle on for when you miss the warmth.
    </p>
  )
}
