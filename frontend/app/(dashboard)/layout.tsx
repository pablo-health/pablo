// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Sidebar } from "@/components/layout/Sidebar"
import { Header } from "@/components/layout/Header"
import { redirect } from "next/navigation"
import { mockUser } from "@/lib/mockData"
import { getBAAStatus } from "@/lib/api/users"
import { getCachedUserStatus } from "@/lib/api/users.server"
import { getServerSession } from "@/lib/auth/server"
import { DashboardErrorBoundary } from "@/components/DashboardErrorBoundary"
import { IdleTimeout } from "@/components/IdleTimeout"
import { ThemeSync } from "@/components/theme/ThemeSync"

export const dynamic = "force-dynamic"

// DEV_MODE bypasses auth and renders with a mock user. Gate it on
// NODE_ENV too so a stray DEV_MODE=true on a production revision can
// never disable the auth/MFA/BAA gate — the bypass branch is dead code
// in a production build.
const IS_DEV_MODE =
  process.env.DEV_MODE === "true" && process.env.NODE_ENV !== "production"
const IS_OSS_EDITION = (process.env.PABLO_EDITION || "core") === "core"

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  let user
  let token: string | undefined
  let isAdmin = false

  if (IS_DEV_MODE) {
    user = mockUser
    isAdmin = true
  } else {
    const session = await getServerSession()
    if (!session) {
      redirect("/login")
    }

    const { claims } = session
    token = session.token

    // Check user status and MFA enrollment
    // Uses /api/users/me/status which does NOT require MFA (pre-enrollment check)
    // SECURITY: Fail-closed — any error blocks access
    // NOTE: redirect() throws NEXT_REDIRECT — must re-throw to avoid catch swallowing it
    try {
      const userStatus = await getCachedUserStatus(token)

      // Use backend user data for display (token claims may be stripped by auth edge)
      user = {
        name: userStatus.name || claims.name || claims.email,
        email: userStatus.email || claims.email,
        image: claims.picture,
      }
      isAdmin = userStatus.is_platform_admin

      // Disabled users cannot access the platform
      if (userStatus.status === "disabled") {
        redirect("/login?error=account_disabled")
      }

      // MFA not enrolled → redirect to enrollment page
      // Skip if MFA is not required (local development)
      if (process.env.REQUIRE_MFA !== "false" && !userStatus.mfa_enrolled_at) {
        redirect("/mfa-enrollment")
      }
    } catch (error) {
      if (error && typeof error === "object" && "digest" in error) throw error
      console.error("Failed to check user status — blocking access:", error)
      redirect("/login")
    }

    // Check BAA acceptance status (managed editions only).
    // OSS self-hosters sign the BAA directly with Google Cloud, not in-app.
    // SECURITY: This is fail-closed - any error blocks access
    if (!IS_OSS_EDITION) {
      try {
        const baaStatus = await getBAAStatus(token)
        if (!baaStatus.accepted || baaStatus.version !== baaStatus.current_version) {
          redirect("/baa-acceptance")
        }
      } catch (error) {
        if (error && typeof error === "object" && "digest" in error) throw error
        console.error("Failed to check BAA status — blocking access:", error)
        redirect("/baa-acceptance")
      }
    }
  }

  return (
    <div className="flex h-screen">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:top-4 focus:left-4 focus:bg-white focus:px-4 focus:py-2 focus:rounded-lg focus:shadow-lg focus:text-primary-700 focus:font-medium"
      >
        Skip to main content
      </a>
      <Sidebar isAdmin={isAdmin} />
      <div className="flex flex-1 flex-col">
        <Header user={user} />
        <main id="main-content" className="flex-1 overflow-y-auto p-6 bg-neutral-50">
          <DashboardErrorBoundary>{children}</DashboardErrorBoundary>
        </main>
      </div>
      <IdleTimeout />
      <ThemeSync />
    </div>
  )
}
