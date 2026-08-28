// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding step — working hours.
 *
 * The last (optional) step in the stock surface: pick the weekdays the
 * clinician sees clients and one time range applied to all of them.
 * Saving creates one `working_hours` availability rule per selected day
 * through the same API the settings surface uses; skipping creates
 * nothing. Either path marks onboarding_state "completed" so the
 * resolver moves on to /dashboard.
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { OnboardingStepShell } from "@/components/onboarding/OnboardingStepShell"
import { ScheduleStep } from "@/components/onboarding/ScheduleStep"

export const dynamic = "force-dynamic"

export default async function OnboardingSchedulePage() {
  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  // Already finished this step? Let the wizard index recompute and move
  // on — guards against direct navigation.
  const status = await getUserStatus(tokens.token)
  if (status.onboarding_state === "completed") {
    redirect("/onboarding")
  }

  return (
    <OnboardingStepShell
      stepId="schedule"
      title="Set your working hours"
      description="Pick the days you see clients and the hours you're available. You can always fine-tune this later in Settings — or skip it for now."
    >
      <ScheduleStep />
    </OnboardingStepShell>
  )
}
