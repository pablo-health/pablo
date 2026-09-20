// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The crisis line, shown on every measure and on the receipt.
 *
 * Unconditional. See `formsCopy.ts` for why it is never keyed off an answer.
 */

import { CRISIS_FOOTER } from "./formsCopy"

export function CrisisFooter() {
  return (
    <p
      data-testid="forms-crisis-footer"
      className="mt-6 rounded-md bg-neutral-100 px-3 py-3 text-xs leading-relaxed text-neutral-700"
    >
      {CRISIS_FOOTER}
    </p>
  )
}
