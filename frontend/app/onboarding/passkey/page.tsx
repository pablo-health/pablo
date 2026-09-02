// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding step — passkey-first second factor.
 *
 * Leads the security step with a one-tap passkey (Face ID / Touch ID /
 * security key) instead of an authenticator-app QR + 6-digit code. A
 * verified passkey is a phishing-resistant second factor, so the backend
 * stamps mfa_enrolled_at on enrolment (#488) — the same gate this step
 * and the dashboard read.
 *
 * When the active onboarding surface includes an authenticator-app
 * (TOTP) step, that flow is offered as a fallback via a link to
 * /onboarding/mfa; deployments whose auth backend can't enrol TOTP omit
 * it. When PASSKEYS_ENABLED is off this page hands straight to the TOTP
 * step.
 */

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { redirect } from "next/navigation"
import { getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { getOnboardingSurface } from "@/lib/onboarding/surface"
import { IS_DEV_MODE } from "@/lib/devMode"
import { OnboardingStepShell } from "@/components/onboarding/OnboardingStepShell"
import { OnboardingPasskeyForm } from "@/components/onboarding/OnboardingPasskeyForm"

export const dynamic = "force-dynamic"

const PASSKEYS_ENABLED = process.env.PASSKEYS_ENABLED === "true"

export default async function OnboardingPasskeyPage() {
  // Dev mode skips the second factor entirely (matches the MFA step).
  if (IS_DEV_MODE) {
    redirect("/dashboard")
  }

  // Editions that ship without passkeys go straight to the TOTP step.
  if (!PASSKEYS_ENABLED) {
    redirect("/onboarding/mfa")
  }

  const tokens = await getTokens(await cookies(), authConfig)
  if (!tokens) {
    redirect("/login")
  }

  // Already have a second factor (passkey or TOTP)? Let the wizard index
  // recompute and move on — guards against direct navigation.
  const status = await getUserStatus(tokens.token)
  if (status.mfa_enrolled_at) {
    redirect("/onboarding")
  }

  // Only offer the authenticator-app fallback when the active surface
  // actually has a TOTP step — a deployment on a backend that can't
  // enrol TOTP shouldn't advertise a dead-end.
  const hasTotpStep = getOnboardingSurface().steps.some((s) => s.id === "mfa")

  return (
    <OnboardingStepShell
      stepId="passkey"
      title="Set up a passkey"
      description="Pablo requires a second factor on every account — it keeps your account safe even if your password is ever stolen or phished. A passkey is the easiest way: use your device's fingerprint, face, or screen lock — no authenticator app, no codes to copy, and nothing to phish."
    >
      <OnboardingPasskeyForm showTotpFallback={hasTotpStep} />
    </OnboardingStepShell>
  )
}
