// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { mockUser } from "@/lib/mockData"
import { getServerSession } from "@/lib/auth/server"
import { getCachedUserStatus } from "@/lib/api/users.server"
import { CompliancePanel } from "@/components/compliance/CompliancePanel"
import { AwaitingReviewPanel } from "@/components/dashboard/AwaitingReviewPanel"
import { DashboardBanners } from "@/components/dashboard/DashboardBanners"
import { DashboardGreeting } from "@/components/dashboard/DashboardGreeting"
import { TodayPanel } from "@/components/dashboard/TodayPanel"
import { WeekPanel } from "@/components/dashboard/WeekPanel"

// Gated on NODE_ENV so DEV_MODE can never bypass auth in a production
// build (see app/(dashboard)/layout.tsx for the rationale).
const IS_DEV_MODE =
  process.env.DEV_MODE === "true" && process.env.NODE_ENV !== "production"

export default async function DashboardPage() {
  let user
  let isPlatformAdmin = false

  if (IS_DEV_MODE) {
    user = mockUser
  } else {
    const session = await getServerSession()
    const claims = session?.claims
    user = {
      name: claims?.name || claims?.email,
      email: claims?.email,
      image: claims?.picture,
    }
    if (session?.token) {
      try {
        const status = await getCachedUserStatus(session.token)
        isPlatformAdmin = status.is_platform_admin
        user = {
          name: status.name || user.name,
          email: status.email || user.email,
          image: claims?.picture,
        }
      } catch {
        // Layout already gates access on this call; fall through to clinician view.
      }
    }
  }

  return (
    <div className="space-y-6">
      <DashboardGreeting name={user?.name} />

      {isPlatformAdmin ? (
        <PlatformAdminPanel />
      ) : (
        <>
          <DashboardBanners />
          <AwaitingReviewPanel />
          <TodayPanel />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <WeekPanel />
            <CompliancePanel />
          </div>
        </>
      )}
    </div>
  )
}

function PlatformAdminPanel() {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-6">
      <h2 className="text-xl font-display font-semibold text-neutral-900">
        Platform admin
      </h2>
      <p className="mt-2 text-neutral-600">
        You&rsquo;re signed in as a platform admin. Clinician panels (today,
        week, compliance) are hidden because they require BAA acceptance and
        access to patient records. Use the admin navigation in the sidebar to
        manage users and platform settings.
      </p>
    </div>
  )
}
