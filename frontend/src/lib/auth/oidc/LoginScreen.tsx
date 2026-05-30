// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * OIDC implementation of the `/login` surface.
 *
 * Keycloak hosts its own login UI, so this component is a thin shell that
 * immediately initiates an auth-code + PKCE redirect via `signIn()`. No
 * password fields and no ROPC — credentials never touch this app.
 *
 * A loading spinner is shown while the redirect is in flight so the user
 * sees feedback rather than a blank flash.
 */

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { signIn } from "next-auth/react"
import { useAuth } from "@/lib/auth-context"

export function OidcLoginScreen() {
  const router = useRouter()
  const { user, loading: authLoading } = useAuth()
  const [redirecting, setRedirecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // If already authenticated, go straight to dashboard.
  useEffect(() => {
    if (!authLoading && user) {
      router.replace("/dashboard")
    }
  }, [user, authLoading, router])

  const handleSignIn = async () => {
    setRedirecting(true)
    setError(null)
    try {
      await signIn("keycloak", { callbackUrl: "/dashboard" })
    } catch {
      setRedirecting(false)
      setError("Failed to initiate sign-in. Please try again.")
    }
  }

  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-primary-50 via-neutral-50 to-secondary-50">
      <div className="w-full max-w-sm space-y-6 bg-white p-10 rounded-2xl shadow-xl border border-neutral-100 text-center">
        <h1 className="text-4xl font-display font-bold text-primary-600">Pablo</h1>
        <p className="text-neutral-600">Sign in to your account</p>

        {error && (
          <p className="rounded-lg bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-700">
            {error}
          </p>
        )}

        <button
          onClick={handleSignIn}
          disabled={redirecting}
          className="w-full bg-primary-600 text-white px-6 py-3 rounded-lg font-medium hover:bg-primary-700 active:scale-[0.98] transition-all duration-200 disabled:opacity-60 flex items-center justify-center gap-2"
        >
          {redirecting ? (
            <>
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
              Redirecting to sign-in…
            </>
          ) : (
            "Sign In"
          )}
        </button>
      </div>
    </div>
  )
}
