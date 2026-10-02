// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Puts a practice's theme on the portal beneath it: the theme's stylesheet,
 * and an element for it to apply to. Only tokens change — colors, fonts and
 * the corner radius — so the portal's layout and its accessibility stay as
 * they are.
 *
 * With no theme it renders its children and nothing else, so the portal looks
 * exactly as it does without one.
 */

import { type PracticeTheme, THEME_ATTRIBUTE, practiceThemeCss } from "@/lib/portal-host/practice-theme"

export function PracticeThemeScope({ theme, children }: { theme: PracticeTheme | null; children: React.ReactNode }) {
  if (theme === null) return children
  return (
    <div {...{ [THEME_ATTRIBUTE]: "" }} data-testid="practice-theme">
      {/* Set as HTML so the quotes around a font name reach the browser as
          quotes. Safe: practiceThemeCss writes only checked hex colors, listed
          fonts and listed radii. */}
      <style dangerouslySetInnerHTML={{ __html: practiceThemeCss(theme) }} />
      {children}
    </div>
  )
}
