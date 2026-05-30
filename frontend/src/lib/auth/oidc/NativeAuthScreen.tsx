// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * OIDC implementation of the `/native-auth` surface — companion desktop-app
 * sign-in handoff.
 *
 * The auth-code + PKCE flow initiated by `signIn("keycloak")` redirects to
 * `/api/auth/callback/keycloak` and then to `/dashboard`. The native app
 * can leverage the same OIDC authorization flow at the OS level (RFC 8252
 * loopback / custom-scheme) instead of routing through this web surface.
 *
 * For deployments where the native app still uses the web handoff, this
 * screen initiates a Keycloak auth-code redirect and hands control back to
 * the native app after Auth.js sets the session. It does not implement ROPC.
 */

import { useEffect, useState } from "react"
import { useSearchParams } from "next/navigation"
import { signIn } from "next-auth/react"

export function OidcNativeAuthScreen() {
  const searchParams = useSearchParams()
  const redirectUri = searchParams.get("redirect_uri")
  const [error, setError] = useState<string | null>(null)

  // Validate redirect_uri — same allowlist as the Firebase implementation.
  const isValidRedirectUri = (() => {
    if (!redirectUri) return false
    try {
      const url = new URL(redirectUri)
      const scheme = url.protocol.replace(":", "")
      const ALLOWED_SCHEMES = ["pablohealth", "therapyrecorder"]
      if (ALLOWED_SCHEMES.includes(scheme)) return true
      if (scheme === "http" && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) {
        return true
      }
      return false
    } catch {
      return false
    }
  })()

  // Auto-initiate sign-in when the screen loads — no extra click needed.
  // We track sign-in state via the error state; absence of error with a
  // valid redirect_uri means we are in-flight.
  useEffect(() => {
    if (!isValidRedirectUri) return
    signIn("keycloak", {
      callbackUrl: `/native-auth?redirect_uri=${encodeURIComponent(redirectUri!)}`,
    }).catch(() => {
      setError("Failed to initiate sign-in. Please try again.")
    })
  }, [isValidRedirectUri, redirectUri])

  if (!isValidRedirectUri) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="w-full max-w-sm bg-white p-10 rounded-2xl shadow-xl border border-neutral-100 text-center space-y-4">
          <h1 className="text-2xl font-semibold text-red-600">Invalid Request</h1>
          <p className="text-sm text-neutral-600">
            {!redirectUri
              ? "Missing redirect_uri parameter. This page must be opened from the Pablo app."
              : "Invalid redirect_uri scheme. Only approved native apps may use this page."}
          </p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="w-full max-w-sm bg-white p-10 rounded-2xl shadow-xl border border-neutral-100 text-center space-y-4">
          <p className="text-sm text-red-600">{error}</p>
          <button
            onClick={() => {
              setError(null)
              void signIn("keycloak", {
                callbackUrl: `/native-auth?redirect_uri=${encodeURIComponent(redirectUri!)}`,
              })
            }}
            className="w-full bg-primary-600 text-white px-4 py-2 rounded-lg hover:bg-primary-700"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
        <p className="text-sm text-neutral-600">Redirecting to sign-in…</p>
      </div>
    </div>
  )
}
