// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding wizard chrome — outer background only.
 *
 * The step card lives inside `OnboardingStepShell`, not here. Auth
 * check runs once here for every step page — those pages assume the
 * auth gate cleared and don't re-check.
 */

import { redirect } from "next/navigation"
import { getServerSession } from "@/lib/auth/server"
import { IdleTimeout } from "@/components/IdleTimeout"

export const dynamic = "force-dynamic"

export default async function OnboardingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const session = await getServerSession()
  if (!session) {
    redirect("/login")
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50 flex items-center justify-center p-6">
      {/* Onboarding is a sibling route group to (dashboard), so it doesn't
          inherit the dashboard layout's IdleTimeout. Mount it here too so
          every authenticated screen auto-logs-off on inactivity. */}
      <IdleTimeout />
      {children}
    </div>
  )
}
