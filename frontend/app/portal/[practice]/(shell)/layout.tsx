// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal shell, as the layout of Home (`/portal/{slug}`) and of
 * every section (`/portal/{slug}/{section}`).
 *
 * A layout rather than part of each page because it holds the session: the
 * shell stays mounted while the patient moves between Home and a section,
 * so the slug is resolved and the session proved once per visit rather than
 * on every tap. The pages beneath it only say which view to draw.
 *
 * A thin server wrapper: the dynamic segment is handed straight to a client
 * component that does the fetch-on-mount, so this file stays a server
 * component (needed to `await params`) without fetching slug-derived data
 * itself.
 *
 * `(shell)` is a route group, so it adds nothing to the address. It exists
 * to keep `recover/` — the page for somebody with no session — out from
 * under a shell that would try to find one.
 *
 * Reachability: `/portal` is a built-in public path
 * (`src/lib/auth/public-paths.ts`), so the auth middleware lets an
 * unauthenticated patient through instead of redirecting to `/login`. The
 * person on these pages holds a portal session, never a clinician one.
 *
 * On a deployment that serves the portal on a host of its own (PORTAL_HOSTS),
 * these routes are also reached there as `/{slug}` and `/{slug}/{section}`:
 * the proxy rewrites them here, so `params` carries the same slug either way.
 */

import { PortalShell } from "@/components/portal-shell/PortalShell"

interface LayoutProps {
  children: React.ReactNode
  params: Promise<{ practice: string }>
}

export default async function PortalShellLayout({ children, params }: LayoutProps) {
  const { practice } = await params
  return <PortalShell slug={practice}>{children}</PortalShell>
}
