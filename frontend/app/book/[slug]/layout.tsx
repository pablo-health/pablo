// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The public booking pages wear the practice's theme on the practice's own
 * host, where the proxy serves them only for that practice's links
 * (`@/lib/portal-host/practice-host`). Everywhere else this renders its
 * children and nothing more.
 */

import { headers } from "next/headers"
import { PracticeThemeScope } from "@/components/portal-shell/PracticeThemeScope"
import { practiceHostOf } from "@/lib/portal-host/practice-host-request"

export default async function BookingLayout({ children }: { children: React.ReactNode }) {
  const found = await practiceHostOf((await headers()).get("host"))
  return <PracticeThemeScope theme={found?.theme ?? null}>{children}</PracticeThemeScope>
}
