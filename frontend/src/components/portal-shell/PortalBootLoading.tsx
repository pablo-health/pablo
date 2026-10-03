// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a portal visitor sees while the app's runtime config loads, before the
 * shell or the practice's theme exists. It is drawn on the shell's own
 * background in neutral greys with no accent color, so the moment before the
 * theme applies reads as the same page settling rather than a different
 * product. It says nothing about what is loading: to a client it is simply
 * the page.
 */

import { Loader2 } from "lucide-react"

export function PortalBootLoading() {
  return (
    <div
      data-testid="portal-boot-loading"
      role="status"
      className="flex min-h-screen flex-col items-center justify-center gap-3 bg-neutral-50 text-neutral-400"
    >
      <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
      <p className="text-sm text-neutral-500">Loading…</p>
    </div>
  )
}
