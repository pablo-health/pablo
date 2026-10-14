// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

// The root boundary replaces the root layout, html and body included, so it
// brings its own stylesheet and theme attribute rather than inheriting them.
import "./globals.css"
import { ErrorScreen } from "@/components/errors/ErrorScreen"
import { DEFAULT_THEME } from "@/lib/theme"

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  // A crash at the root usually comes from app-wide state (auth, config), which
  // re-rendering in place would hit again; a full reload starts that over.
  return (
    <html lang="en" data-theme={DEFAULT_THEME}>
      <body className="font-sans">
        <ErrorScreen
          error={error}
          onRetry={() => window.location.reload()}
          logLabel="Global application error"
        />
      </body>
    </html>
  )
}
