// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal's footer. On the practice's own host the portal is part of the
 * practice's site, so it carries no mark of the software behind it; everywhere
 * else it says what it runs on.
 */

"use client"

import { usePortalHost } from "./portal-host-context"

export function PortalFooter() {
  const { onPracticeHost } = usePortalHost()
  if (onPracticeHost) return null
  return (
    <footer data-testid="portal-footer" className="px-4 py-6 text-center text-xs text-neutral-400">
      <p>Powered by Pablo</p>
    </footer>
  )
}
