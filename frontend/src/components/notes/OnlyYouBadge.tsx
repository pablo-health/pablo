// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * OnlyYouBadge
 *
 * The one mark a restricted note carries wherever it is listed: a
 * psychotherapy note is readable by its author alone, and the badge says
 * so in two words. Rendered beside the note type; nothing else on the
 * screen explains it.
 */

import { Lock } from "lucide-react"

export function OnlyYouBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded bg-neutral-800 px-2 py-0.5 text-xs font-medium text-white"
      data-testid="only-you-badge"
    >
      <Lock className="h-3 w-3" aria-hidden="true" />
      Only you
    </span>
  )
}
