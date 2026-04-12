// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { cookies } from "next/headers"
import { getTokens } from "next-firebase-auth-edge"
import { Sidebar } from "@/components/layout/Sidebar"
import { Header } from "@/components/layout/Header"
import { redirect } from "next/navigation"
import { mockUser } from "@/lib/mockData"
import { getBAAStatus, getUserStatus } from "@/lib/api/users"
import { authConfig } from "@/lib/auth-config"
import { DashboardErrorBoundary } from "@/components/DashboardErrorBoundary"
import { setTenantHeader } from "@/lib/api/client"

export const dynamic = "force-dynamic"

const IS_DEV_MODE = process.env.DEV_MODE === "true"

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
    const tokens = await getTokens(await cookies(), authConfig)
    if (!tokens) {
      redirect("/login")
    }

    const { decodedToken } = tokens
    token = tokens.token

    // Pass tenant ID to backend API calls (token may lack firebase.tenant after refresh)
    const cookieStore = await cookies()
    const tenantCookie = cookieStore.get("X-Tenant-ID")
    if (tenantCookie?.value) {
      setTenantHeader(tenantCookie.value)
    }

    // Check user status and MFA enrollment
    // Uses /api/users/me/status which does NOT require MFA (pre-enrollment check)
    // SECURITY: Fail-closed — any error blocks access
    // NOTE: redirect() throws NEXT_REDIRECT — must re-throw to avoid catch swallowing it
    try {
      const userStatus = await getUserStatus(token)

      // Use backend user data for display (token claims may be stripped by auth edge)
      user = {
        name: userStatus.name || decodedToken.name || decodedToken.email,
        email: userStatus.email || decodedToken.email,
        image: decodedToken.picture,
      }
      isAdmin = userStatus.is_admin

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
      console.error("Failed to check user status — blocking access")
      redirect("/login")
    }

    // Check BAA acceptance status
    // SECURITY: This is fail-closed - any error blocks access
    try {
      const baaStatus = await getBAAStatus(token)
      if (!baaStatus.accepted || baaStatus.version !== baaStatus.current_version) {
        redirect("/baa-acceptance")
      }
    } catch (error) {
      if (error && typeof error === "object" && "digest" in error) throw error
      console.error("Failed to check BAA status — blocking access")
      redirect("/baa-acceptance")
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
    </div>
  )
}
