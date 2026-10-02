// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { SiteTheme, SiteThemeReport } from "@/lib/api/practiceSite"

/** "colors, fonts and corner style" — the parts of the portal a theme sets. */
export function themeParts(theme: SiteTheme | null): string | null {
  if (!theme) return null
  const parts = [
    Object.values(theme.colors).some(Boolean) && "colors",
    Object.values(theme.fonts).some(Boolean) && "fonts",
    theme.radius && "corner style",
  ].filter((part): part is string => Boolean(part))
  if (parts.length === 0) return null
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`
}

/**
 * What the draft's `theme.json` gives the portal, and what it leaves out and
 * why. The reasons come from the backend (backend/app/sites/theme.py), written
 * for whoever made the website. A theme only shows on the practice's own portal
 * domain, which is why the sentence names it.
 */
export function ThemeReport({ report }: { report: SiteThemeReport }) {
  const parts = themeParts(report.theme)
  return (
    <div data-testid="website-theme" className="mt-3 space-y-1 text-[12.5px]">
      {parts && (
        <p>Once this is published, your portal on your own domain will use the {parts} from theme.json.</p>
      )}
      {report.skipped.length > 0 && (
        <>
          <p className="text-muted-foreground">Not used from theme.json:</p>
          <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
            {report.skipped.map((skipped) => (
              <li key={skipped.field}>
                <code>{skipped.field}</code>: {skipped.reason}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
