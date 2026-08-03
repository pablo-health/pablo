// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the `/login` surface. Credential acquisition
 * (email/password, Google, passkey, MFA, verification) lives in the shared
 * `CredentialBlock`; this host owns what comes after a credential resolves —
 * seeding the session cookie and routing to the dashboard — plus the branded
 * chrome, forced-logout notices, and the setup-token prefill. Rendered
 * through `getAuthSurfaces().LoginScreen`; the `/login` route is a thin
 * shell that picks this per the active provider.
 */

import { useState, useEffect } from "react"
import Image from "next/image"
import { useRouter } from "next/navigation"
import type { UserCredential } from "firebase/auth"
import { getFirebaseAuth } from "@/lib/firebase"
import { clearStaleSession } from "./client"
import { useAuth } from "@/lib/auth-context"
import {
  consumeRecoveryNotice,
  installAuthRecovery,
} from "@/lib/firebaseAuthRecovery"
import {
  AuthCard,
  AuthFooter,
  AuthHeader,
  CredentialBlock,
} from "@/components/auth"
import { ThemeSwitcher } from "@/components/theme/ThemeSwitcher"

function getUrlParam(name: string): string {
  if (typeof window === "undefined") return ""
  const params = new URLSearchParams(window.location.search)
  return params.get(name) || ""
}

export function FirebaseLoginScreen() {
  const router = useRouter()
  const { user, loading: authLoading } = useAuth()

  const [email, setEmail] = useState("")
  const [isSignUp, setIsSignUp] = useState(false)

  // Whether this mount was reached via a forced logout — an idle timeout, or a
  // session whose token expired/was revoked. Captured synchronously (before the
  // effect below strips the param) so the "already authenticated → /dashboard"
  // redirect can't bounce a restored-but-stale session straight back into the
  // 401 loop.
  const [forcedLogoutReason] = useState(() => getUrlParam("reason"))
  const cameFromForcedLogout =
    forcedLogoutReason === "idle_timeout" ||
    forcedLogoutReason === "session_expired"

  // Message surfaced in the credential block's error slot. Seeded with the
  // forced-logout notice, which is knowable at first render.
  const [notice, setNotice] = useState(() => {
    if (!cameFromForcedLogout) return ""
    return forcedLogoutReason === "idle_timeout"
      ? "You were signed out due to inactivity."
      : "Your session expired. Please sign in again."
  })

  // Clean up after a forced logout: strip the reason param and clear the
  // stale session.
  useEffect(() => {
    if (!cameFromForcedLogout) return
    window.history.replaceState({}, "", "/login")
    // Backstop: the logout path already wipes the persisted session, but clear
    // again here in case that raced or was bypassed. Otherwise a session the
    // SDK re-hydrates on this page would auto-redirect to the dashboard and
    // re-trip the same 401. Forces a fresh sign-in.
    void clearStaleSession(getFirebaseAuth())
  }, [cameFromForcedLogout])

  // Arm the Firebase Auth stuck-state recovery and surface a one-line
  // notice if the last attempt was auto-recovered. THERAPY-n1n6.
  useEffect(() => {
    installAuthRecovery()
    // The recovery flag is a consume-once external read (it clears on read),
    // so it can't move into a render-time initializer.
    if (consumeRecoveryNotice()) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNotice(
        "We cleared a stuck sign-in state from a previous attempt. Please try signing in again."
      )
    }
  }, [])

  // Exchange setup token from marketing signup to pre-fill email
  useEffect(() => {
    const setupToken = getUrlParam("setup")
    if (!setupToken) return

    // Clean the URL immediately so the token isn't in browser history
    window.history.replaceState({}, "", "/login")

    fetch("/api/auth/exchange-setup-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: setupToken }),
    })
      .then(res => res.json())
      .then(data => {
        if (data.email) {
          setEmail(data.email)
          setIsSignUp(true)
          // Override browser autofill by setting DOM value directly after a tick
          setTimeout(() => {
            const emailEl = document.getElementById("email") as HTMLInputElement | null
            if (emailEl) emailEl.value = data.email
            document.getElementById("password")?.focus()
          }, 200)
        }
      })
      .catch((err) => {
        // Token expired or invalid — user types email manually. Logged so
        // backend exchange failures (e.g. the tenant-isolation trigger fires
        // on /api/auth/exchange-setup-token) surface in the user's console.
        console.error("exchange-setup-token failed:", err)
      })
  }, [])

  // Redirect to dashboard when already authenticated (but not during signup
  // flow — including its verify-email step, which keeps isSignUp true — and
  // not when we arrived here from a forced logout: that session is stale and
  // being cleared; bouncing back would re-enter the 401 loop). A genuine
  // re-login navigates via finishLogin(), not this effect.
  useEffect(() => {
    if (user && !authLoading && !isSignUp && !cameFromForcedLogout) {
      router.push("/dashboard")
    }
  }, [user, authLoading, router, isSignUp, cameFromForcedLogout])

  const finishLogin = async (credential: UserCredential) => {
    const idToken = await credential.user.getIdToken()
    // Send the refresh token so the server can seed the session cookie
    // directly, without minting a service-account-signed custom token to
    // exchange for one. That SA-signing path needs the GCP metadata server,
    // which isn't reachable on non-GCP hosts (e.g. AWS); the server verifies
    // the idToken and confirms this refresh token resolves to the same uid
    // before trusting it.
    const refreshToken = credential.user.refreshToken
    await fetch("/api/login", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${idToken}`,
        "X-Refresh-Token": refreshToken,
      },
    })
    router.push("/dashboard")
  }

  return (
    <CredentialBlock
      onCredential={finishLogin}
      email={email}
      onEmailChange={setEmail}
      showLastUsed
      showAuthReset
      notice={notice}
      signUp={isSignUp}
      onSignUpChange={setIsSignUp}
      renderShell={(form) => (
        <AuthCard brandPanel={<LoginBrandPanel />}>
          <div className="mb-6 flex flex-col items-center gap-2 lg:hidden">
            <div className="flex items-center gap-2.5">
              <Image src="/pablo-login.webp" alt="" width={44} height={44} className="object-contain" />
              <span className="font-display text-2xl font-bold text-primary-600">Pablo</span>
            </div>
            <span className="text-xs text-neutral-500">AI documentation for mental health clinicians</span>
          </div>

          <AuthHeader
            title={isSignUp ? "Create your account" : "Welcome back"}
            titleSize="4xl"
            titleColor="foreground"
          />

          {form}

          <AuthFooter />

          {/* Brand panel (with the theme picker) is hidden on mobile, so offer it here. */}
          <div className="mt-6 flex justify-center lg:hidden">
            <div className="flex flex-col items-center gap-2">
              <span className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-neutral-500">
                Theme
              </span>
              <ThemeSwitcher />
            </div>
          </div>
        </AuthCard>
      )}
    />
  )
}

function LoginBrandPanel() {
  const points = [
    "AI drafts your SOAP notes from the session for you to review, edit, and finalize",
    "Chat right on a patient's chart to get answers in context",
    "Compliance items and notes to finalize, tracked in one place",
    "HIPAA-compliant by design",
  ]
  return (
    <>
      <div>
        <span
          className="mb-2.5 block text-xs font-semibold uppercase tracking-[0.12em]"
          style={{ color: "var(--brand-panel-accent)" }}
        >
          For mental health clinicians
        </span>
        <span className="font-display text-3xl font-bold">Pablo</span>
        <p
          className="mt-6 max-w-xs font-display text-2xl leading-snug"
          style={{ color: "var(--brand-panel-fg)" }}
        >
          Let AI carry the documentation, so your evenings and weekends are yours again.
        </p>
        <div className="mt-8 flex flex-col gap-2">
          <span className="text-[0.6875rem] font-semibold uppercase tracking-[0.09em] text-brand-panel-muted">
            Theme
          </span>
          <ThemeSwitcher />
        </div>
      </div>
      <div className="flex flex-1 items-center justify-center py-8">
        <Image
          src="/pablo-login.webp"
          alt="Pablo, the friendly bear in a tie"
          width={240}
          height={240}
          priority
          className="drop-shadow-2xl"
        />
      </div>
      <ul className="space-y-3 text-sm">
        {points.map((point) => (
          <li key={point} className="flex items-start gap-3">
            <span style={{ color: "var(--brand-panel-accent)" }}>✦</span>
            <span style={{ color: "var(--brand-panel-muted)" }}>{point}</span>
          </li>
        ))}
      </ul>
    </>
  )
}
