// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { Sidebar } from "@/components/layout/Sidebar"
import { Header } from "@/components/layout/Header"
import { redirect } from "next/navigation"
import { mockUser } from "@/lib/mockData"
import { ApiError } from "@/lib/api/client"
import { getBAAStatus } from "@/lib/api/users"
import { getCachedUserStatus } from "@/lib/api/users.server"
import { getServerSession } from "@/lib/auth/server"
import { IS_DEV_MODE } from "@/lib/devMode"
import { getOnboardingSurface } from "@/lib/onboarding/surface"
import { firstIncompleteRequiredStep } from "@/lib/onboarding/types"
import { DashboardErrorBoundary } from "@/components/DashboardErrorBoundary"
import { IdleTimeout } from "@/components/IdleTimeout"
import { ThemeSync } from "@/components/theme/ThemeSync"
import { errorCode } from "@/lib/errors/errorCode"

export const dynamic = "force-dynamic"

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

      // Onboarding gate — route to the active surface's first incomplete
      // required step before the MFA safety-net below. For the default
      // surface this only fires when passkeys are enabled (its sole
      // required step); otherwise the surface is empty and vanilla
      // deployments fall straight through to /mfa-enrollment.
      if (firstIncompleteRequiredStep(getOnboardingSurface(), userStatus) !== null) {
        redirect("/onboarding")
      }

      // Second-factor safety net. Gate on whether THIS SESSION cleared a
      // factor, not on whether the account has ever enrolled one.
      //
      // `mfa_enrolled_at` is stamped once at first enrolment and never
      // cleared, so it answers "has a factor" — and the onboarding step
      // above already refuses to let anyone past without it. Gating here on
      // the same field therefore catches nobody: a user who enrolled a
      // passkey and then signed in with a password or Google carries that
      // stamp, sails through, and reaches a dashboard where every PHI route
      // 403s them. A passkey is Pablo's factor and invisible to Firebase, so
      // no MFA challenge fires on those paths and nothing else notices.
      //
      // In UI flows the onboarding step above means an unsatisfied session
      // here is almost always "has a factor, didn't use it" — so the answer
      // is to ask for it, not to send them to enrol something they already
      // have. That gate is a client-side redirect though, not a control: a
      // programmatic caller can hold a valid first-factor token and never
      // pass through it. So branch on what we can actually observe about the
      // account rather than on an assumption about how it got here. The real
      // enforcement is the backend refusing the token either way.
      if (process.env.REQUIRE_MFA !== "false" && !userStatus.session_mfa_satisfied) {
        // A passkey can be asserted right here without signing out.
        if (userStatus.has_passkey) {
          redirect("/mfa-step-up")
        }
        // No passkey to challenge: `mfa_enrolled_at` is set but nothing backs
        // it (a self-reported TOTP stamp with no Firebase factor — see the
        // warning on the /me/mfa-enrolled route). Enrolment is the only
        // honest destination, and it is where this case landed before.
        redirect("/mfa-enrollment")
      }
    } catch (error) {
      if (error && typeof error === "object" && "digest" in error) throw error
      console.error("Failed to check user status — blocking access:", errorCode(error))
      // A dead session (backend idle timeout / revoked token) must land on
      // /login carrying a reason: the auth cookie is still cryptographically
      // valid at this point (an RSC redirect can't clear it), and without
      // the reason param the middleware's "authenticated user on /login"
      // handling bounces the request straight back to /dashboard — an
      // endless valid-cookie/dead-session loop that looks like a logged-in
      // page. The login screen clears the stale client session itself.
      if (error instanceof ApiError && error.status === 401) {
        redirect(
          error.code === "IDLE_TIMEOUT"
            ? "/login?reason=idle_timeout"
            : "/login?reason=session_expired",
        )
      }
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
        console.error("Failed to check BAA status — blocking access:", errorCode(error))
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
