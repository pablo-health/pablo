// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Accepting an invitation to join a practice, from the emailed link.
 *
 * Public, and outside `(dashboard)`: the dashboard resolves the caller to a
 * practice, and the account accepting has none yet. Nothing here may call a
 * route that does, or the account is placed before the invitation can.
 */

import { InviteAcceptance } from "./InviteAcceptance"

export const dynamic = "force-dynamic"

export default function InvitePage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50 p-6">
      <div className="mx-auto max-w-md pt-16">
        <InviteAcceptance />
      </div>
    </div>
  )
}
