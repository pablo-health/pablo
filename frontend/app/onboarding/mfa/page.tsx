// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding step — MFA enrollment (authenticator-app / TOTP).
 *
 * Wizard-chrome variant of the MFA enrollment flow. First-time signups
 * traversing a multi-step surface see MFA inside the shared
 * OnboardingStepShell — same step counter / typography as the prior
 * steps. The standalone /mfa-enrollment page is preserved for direct
 * navigations and for the dashboard layout's safety-net redirect (the
 * wizard "Step N of M" eyebrow would be misleading for a user who
 * reached /dashboard with an unsatisfied MFA gate).
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { MFAEnrollmentForm } from "@/app/mfa-enrollment/MFAEnrollmentForm"
import { OnboardingStepShell } from "@/components/onboarding/OnboardingStepShell"

export const dynamic = "force-dynamic"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

export default async function OnboardingMFAPage() {
  // Dev mode skips MFA entirely (matches the standalone page).
  if (IS_DEV_MODE) {
    redirect("/dashboard")
  }

  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  // If MFA is already enrolled, the wizard index would not have routed
  // here — but guard against direct navigation by handing back to the
  // index to recompute.
  const status = await getUserStatus(tokens.token)
  if (status.mfa_enrolled_at) {
    redirect("/onboarding")
  }

  return (
    <OnboardingStepShell
      stepId="mfa"
      title="Two-factor authentication is required"
      description="It keeps your account safe even if your password is ever stolen or phished. Link your authenticator app to finish — you'll enter a 6-digit code at sign-in."
      noAside
    >
      <MFAEnrollmentForm returnTo="/onboarding" />
    </OnboardingStepShell>
  )
}
