// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell route — `/portal/{slug}`.
 *
 * A thin server wrapper: the dynamic segment is handed straight to a client
 * component that does the fetch-on-mount, so this file stays a server
 * component (needed to `await params`) without fetching slug-derived data
 * itself.
 *
 * `useSearchParams` (for `?invite=`) inside `PortalShell` requires a
 * `Suspense` boundary, per the Next app-router rule.
 *
 * Reachability: `/portal` is a built-in public path
 * (`src/lib/auth/public-paths.ts`), so the auth middleware lets an
 * unauthenticated patient through instead of redirecting to `/login`. The
 * person on this page holds a portal session, never a clinician one.
 */

import { Suspense } from "react"
import { PortalShell } from "@/components/portal-shell/PortalShell"

interface PageProps {
  params: Promise<{ practice: string }>
}

export default async function PortalShellRoute({ params }: PageProps) {
  const { practice } = await params
  return (
    <Suspense fallback={null}>
      <PortalShell slug={practice} />
    </Suspense>
  )
}
