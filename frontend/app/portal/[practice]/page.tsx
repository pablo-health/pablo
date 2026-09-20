// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell route — `/portal/{slug}`.
 *
 * A thin server wrapper: the dynamic segment is handed straight to a client
 * component that does the fetch-on-mount, so this file stays a server
 * component (needed to `await params`) without fetching slug-derived data
 * itself.
 *
 * An invitation reaches the shell in the URL fragment, which the server
 * never sees — the client component reads it off `location.hash` on mount.
 * That is also why nothing here needs a `Suspense` boundary.
 *
 * Reachability: `/portal` is a built-in public path
 * (`src/lib/auth/public-paths.ts`), so the auth middleware lets an
 * unauthenticated patient through instead of redirecting to `/login`. The
 * person on this page holds a portal session, never a clinician one.
 */

import { PortalShell } from "@/components/portal-shell/PortalShell"

interface PageProps {
  params: Promise<{ practice: string }>
}

export default async function PortalShellRoute({ params }: PageProps) {
  const { practice } = await params
  return <PortalShell slug={practice} />
}
