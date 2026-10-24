// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A portal address that names no practice, answered with a 404.
 *
 * The practice layout (`[practice]/layout.tsx`) asks whether a practice holds
 * the slug and calls `notFound()` when none does, which lands here. A person
 * sees the same card the shell shows for an address it cannot resolve; a
 * crawler or a scanner sees the status, rather than a 200 that reads like a
 * page was there.
 *
 * Only the shared portal address gets here: on a practice's own host the
 * practice is known before the page is, so the footer is the shared one.
 */

import { UnknownPracticeCard } from "@/components/portal-shell/PortalAuthCards"
import { PortalFooter } from "@/components/portal-shell/PortalFooter"

export default function PortalNotFound() {
  return (
    <div className="flex min-h-screen flex-col bg-neutral-50">
      <main className="flex flex-1 items-start justify-center px-4 py-10 sm:py-16">
        <div className="w-full max-w-md">
          <UnknownPracticeCard />
        </div>
      </main>
      <PortalFooter displayName={null} />
    </div>
  )
}
