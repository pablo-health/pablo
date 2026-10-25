// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The app's 404 page, which is also how a portal address that names no
 * practice is answered.
 *
 * The proxy serves such an address at a path no route matches, with the
 * unknown-practice mark set (`@/lib/portal-host/unknown-practice`), so the
 * status is a real 404 and this page shows the card the portal shell shows
 * for an address it cannot resolve. Everything else gets Next's own 404,
 * exactly as before this file existed.
 */

import NextNotFound from "next/dist/client/components/builtin/not-found"
import { headers } from "next/headers"
import { UnknownPracticeCard } from "@/components/portal-shell/PortalAuthCards"
import { PortalFooter } from "@/components/portal-shell/PortalFooter"
import { UNKNOWN_PRACTICE_HEADER } from "@/lib/portal-host/unknown-practice"

export default async function NotFound() {
  if ((await headers()).get(UNKNOWN_PRACTICE_HEADER) === null) return <NextNotFound />
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
