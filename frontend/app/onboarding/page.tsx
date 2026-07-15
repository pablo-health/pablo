// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding index — pure router. Sends the user to the first
 * incomplete step of the active onboarding surface, or to /dashboard if
 * every step is already done.
 *
 * The step list and gating predicates come from the surface resolved by
 * `getOnboardingSurface()`; see `src/lib/onboarding/`.
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { getOnboardingSurface } from "@/lib/onboarding/surface"
import { firstIncompleteStep } from "@/lib/onboarding/types"

export const dynamic = "force-dynamic"

export default async function OnboardingIndex() {
  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  const status = await getUserStatus(tokens.token)
  const next = firstIncompleteStep(getOnboardingSurface(), status)

  if (!next) {
    redirect("/dashboard")
  }

  redirect(next.path)
}
