// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Verify email.
 *
 * Handles a signed-in session whose email the sign-in provider has not
 * verified. The API will not tie such an email to a practice, so the
 * dashboard and onboarding layouts send the session here instead of letting
 * every request fail. It lives outside both layouts because they redirect
 * here; nesting it there would create a redirect loop.
 */

import { redirect } from "next/navigation"
import { getServerSession } from "@/lib/auth/server"
import { needsEmailVerification } from "@/lib/auth/emailVerification"
import { IS_DEV_MODE } from "@/lib/devMode"
import { VerifyEmailForm } from "./VerifyEmailForm"

export const dynamic = "force-dynamic"

export default async function VerifyEmailPage() {
  if (IS_DEV_MODE) {
    redirect("/dashboard")
  }

  const session = await getServerSession()
  if (!session) {
    redirect("/login")
  }
  if (!needsEmailVerification(session.claims)) {
    redirect("/dashboard")
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50 p-6">
      <div className="mx-auto max-w-md pt-16">
        <VerifyEmailForm email={session.claims.email ?? ""} />
      </div>
    </div>
  )
}
