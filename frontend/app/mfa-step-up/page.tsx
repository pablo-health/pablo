// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Passkey step-up.
 *
 * Handles authenticated sessions that have an enrolled passkey but have not
 * satisfied MFA. It lives outside `(dashboard)` because the dashboard layout
 * redirects here; nesting it there would create a redirect loop.
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
