// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Standalone patient-facing chrome for the portal shell.
 *
 * Outside the `(dashboard)` route group deliberately: no clinician nav, no
 * dashboard sidebar. Rides the root layout exactly as `book/[slug]` does —
 * this file gives the route its own metadata and, on a practice's own host,
 * tells the pages beneath that they are there and puts on the practice's
 * theme (`@/lib/portal-host/practice-host-request`). It adds no chrome the
 * root layout already provides.
 *
 * `noindex`: a practice's own slug is not a page worth surfacing in search
 * results, and — unlike `book/[slug]`, which wants to be found — nothing
 * about being found here helps a patient who already has a link.
 */

import type { Metadata } from "next"
import { headers } from "next/headers"
import { PracticeThemeScope } from "@/components/portal-shell/PracticeThemeScope"
import { PortalHostProvider } from "@/components/portal-shell/portal-host-context"
import { portalPracticeHost } from "@/lib/portal-host/practice-host-request"

export const metadata: Metadata = {
  title: "Patient Portal",
  robots: { index: false, follow: false },
}

interface LayoutProps {
  children: React.ReactNode
  params: Promise<{ practice: string }>
}

export default async function PortalLayout({ children, params }: LayoutProps) {
  const { practice } = await params
  const found = await portalPracticeHost((await headers()).get("host"), practice)
  return (
    <PortalHostProvider value={{ onPracticeHost: found !== null }}>
      <PracticeThemeScope theme={found?.theme ?? null}>{children}</PracticeThemeScope>
    </PortalHostProvider>
  )
}
