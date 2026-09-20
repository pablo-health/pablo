// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Standalone patient-facing chrome for the portal shell.
 *
 * Outside the `(dashboard)` route group deliberately: no clinician nav, no
 * dashboard sidebar. Rides the root layout exactly as `book/[slug]` does —
 * this file exists only to give the route its own metadata, not to add
 * chrome the root layout already provides.
 *
 * `noindex`: a practice's own slug is not a page worth surfacing in search
 * results, and — unlike `book/[slug]`, which wants to be found — nothing
 * about being found here helps a patient who already has a link.
 */

import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Patient Portal",
  robots: { index: false, follow: false },
}

export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return children
}
