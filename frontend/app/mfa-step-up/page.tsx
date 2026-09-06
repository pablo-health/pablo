// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Passkey step-up.
 *
 * Reached when a session is authenticated but has NOT cleared a second
 * factor, and the account has a passkey to assert. That state is ordinary,
 * not exotic: a passkey is Pablo's own factor and Firebase knows nothing
 * about it, so signing in with email/password or Google yields a perfectly
 * valid credential carrying no second factor. The account has a strong
 * factor; it simply came in through a door that never asked for it.
 *
 * The fix is to ask for it — here, without signing out — rather than send
 * someone to enrol a factor they already have.
 *
 * Outside the `(dashboard)` route group for the same reason
 * `/mfa-enrollment` is: the dashboard layout is what redirects here, so
 * rendering under it would loop.
 */

import { redirect } from "next/navigation"
import { getServerSession } from "@/lib/auth/server"
import { IS_DEV_MODE } from "@/lib/devMode"
import { PasskeyStepUpForm } from "./PasskeyStepUpForm"

export default async function MfaStepUpPage() {
  if (IS_DEV_MODE) {
    redirect("/dashboard")
  }

  const session = await getServerSession()
  if (!session) {
    redirect("/login")
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50 p-6">
      <div className="mx-auto max-w-md pt-16">
        <PasskeyStepUpForm />
      </div>
    </div>
  )
}
