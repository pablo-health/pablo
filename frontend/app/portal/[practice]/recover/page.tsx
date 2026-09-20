// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal recovery route — `/portal/{slug}/recover`.
 *
 * A thin server wrapper, like the shell route beside it: the dynamic
 * segment is handed straight to a client component that does the
 * fetch-on-mount, so this file stays a server component without fetching
 * slug-derived data itself.
 *
 * Reachability: `/portal` is a built-in public path
 * (`src/lib/auth/public-paths.ts`), so the auth middleware lets through
 * somebody who by definition has no session — which is the whole reason
 * they are on this page.
 */

import { PortalRecover } from "@/components/portal-shell/PortalRecover"

interface PageProps {
  params: Promise<{ practice: string }>
}

export default async function PortalRecoverRoute({ params }: PageProps) {
  const { practice } = await params
  return <PortalRecover slug={practice} />
}
