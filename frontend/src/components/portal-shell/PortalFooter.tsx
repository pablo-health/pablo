// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal's footer. On the practice's own host the portal is part of the
 * practice's site: the footer names the practice and where to turn in a
 * crisis, and carries no mark of the software behind it. Everywhere else it
 * says what it runs on, as it always has.
 */

"use client"

import { usePortalHost } from "./portal-host-context"

/** US crisis lines: 988 (call or text) and 911. */
const CRISIS_LINE = "In crisis? Call or text 988, or call 911."

export function PortalFooter({ displayName }: { displayName: string | null }) {
  const { onPracticeHost } = usePortalHost()
  if (onPracticeHost) {
    return (
      <footer data-testid="portal-footer" className="border-t border-neutral-200 px-4 py-8 text-center text-sm">
        {displayName && <p className="font-medium text-neutral-800">{displayName}</p>}
        <p className="mt-1 text-neutral-600">{CRISIS_LINE}</p>
      </footer>
    )
  }
  return (
    <footer data-testid="portal-footer" className="px-4 py-6 text-center text-xs text-neutral-400">
      <p>Powered by Pablo</p>
    </footer>
  )
}
