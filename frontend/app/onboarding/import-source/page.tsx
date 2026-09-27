// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding step — "Are you importing from another EHR?".
 *
 * Optional and asked once; see ImportSourceStep for what each answer does.
 * Already answered? Hand back to the wizard index so it can move on — guards
 * against direct navigation.
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { OnboardingStepShell } from "@/components/onboarding/OnboardingStepShell"
import { ImportSourceStep } from "@/components/onboarding/ImportSourceStep"

export const dynamic = "force-dynamic"

export default async function OnboardingImportSourcePage() {
  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  const status = await getUserStatus(tokens.token)
  if (status.import_prompted_at) {
    redirect("/onboarding")
  }

  return (
    <OnboardingStepShell
      stepId="import-source"
      title="Are you importing from another EHR?"
      description="If your records are in SimplePractice, you can bring your clients and notes across now or later from Settings."
    >
      <ImportSourceStep />
    </OnboardingStepShell>
  )
}
