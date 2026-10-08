// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Beside a field of an imported note: whether its text was found in the
 * original document (`lib/utils/grounding`). One the reader should check
 * says so.
 */

import { AlertTriangle, Check } from "lucide-react"

export function GroundingBadge({ grounded }: { grounded: boolean }) {
  return grounded ? (
    <span
      className="ml-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 align-middle text-[11px] font-medium text-green-700 bg-green-50"
      title="Found in the original document"
    >
      <Check className="h-3 w-3" />
      from your note
    </span>
  ) : (
    <span
      className="ml-2 inline-flex items-center gap-1 rounded px-1.5 py-0.5 align-middle text-[11px] font-medium text-amber-700 bg-amber-50"
      title="Not found verbatim in the original document — please review"
    >
      <AlertTriangle className="h-3 w-3" />
      review
    </span>
  )
}
